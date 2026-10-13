#!/usr/bin/env python3
"""Keep a saved monitor layout, matched by monitor identity instead of connector name.

After a dock is replugged or the laptop wakes, the kernel can hand out connector names in a different
order (DP-6 and DP-7 swapped on this machine), and KDE then applies saved settings to the wrong monitor:
rotation lost, two monitors on top of each other, windows moved, a panel hidden. This identifies every
monitor by its EDID (manufacturer, product code, serial) and applies the saved layout to whichever
connector each one is on now, using kscreen-doctor.

  z13-display-restore save            store the current layout as the one to keep
  z13-display-restore status          show monitors, their identities, and any difference from the saved layout
  z13-display-restore apply           re-apply the saved layout now (--dry-run prints the command only)
  z13-display-restore watch           background loop: re-apply after the monitor set changes or the machine wakes

The layout lives in ~/.config/z13-display-restore/layout.json (change it with --layout PATH).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

DEFAULT_LAYOUT = os.path.expanduser("~/.config/z13-display-restore/layout.json")
ROTATION_NAMES = {1: "none", 2: "left", 4: "inverted", 8: "right"}
POLL_SECONDS = 2.0
SETTLE_SECONDS = 5.0
WAKE_JUMP_SECONDS = 20.0
MIN_APPLY_GAP = 10.0


# ---- identifying monitors -------------------------------------------------------------------

def parse_edid(blob):
    """Stable identity for a monitor from its EDID bytes, or None if the data is not an EDID."""
    if len(blob) < 128 or blob[:8] != bytes([0, 255, 255, 255, 255, 255, 255, 0]):
        return None
    m = (blob[8] << 8) | blob[9]
    mfg = "".join(chr(((m >> s) & 31) + 64) for s in (10, 5, 0))
    product = blob[10] | (blob[11] << 8)
    serial_num = int.from_bytes(blob[12:16], "little")
    texts = {}
    for off in (54, 72, 90, 108):
        d = blob[off:off + 18]
        if d[:3] == b"\x00\x00\x00" and d[3] in (0xFC, 0xFF):
            texts[d[3]] = d[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
    serial = texts.get(0xFF) or ("%08x" % serial_num)
    return {"id": "%s-%04x-%s" % (mfg, product, serial), "name": texts.get(0xFC, "")}


def connected_monitors(drm_root="/sys/class/drm"):
    """{connector name: {'id':..., 'name':...}} for every connected monitor that has a readable EDID."""
    found = {}
    for path in sorted(glob.glob(os.path.join(drm_root, "card*-*"))):
        try:
            with open(os.path.join(path, "status")) as f:
                if f.read().strip() != "connected":
                    continue
            with open(os.path.join(path, "edid"), "rb") as f:
                blob = f.read()
        except OSError:
            continue
        ident = parse_edid(blob)
        if ident:
            found[os.path.basename(path).split("-", 1)[1]] = ident
    return found


# ---- reading and describing the current layout ----------------------------------------------

def run_kscreen_json():
    out = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=20, check=True).stdout
    return json.loads(out)


def snapshot(kscreen, idmap):
    """The current layout as a list of monitor dicts, keyed by identity."""
    monitors = []
    for o in kscreen["outputs"]:
        ident = idmap.get(o["name"])
        if not ident or not o.get("connected") or not o.get("enabled"):
            continue
        mode = next((m for m in o["modes"] if m["id"] == o.get("currentModeId")), None)
        monitors.append({
            "id": ident["id"],
            "label": ident["name"] or o["name"],
            "connector": o["name"],
            "mode": mode["name"] if mode else None,
            "position": [o["pos"]["x"], o["pos"]["y"]],
            "rotation": ROTATION_NAMES.get(o.get("rotation"), "none"),
            "scale": o.get("scale", 1),
            "priority": o.get("priority"),
        })
    return sorted(monitors, key=lambda m: (m["priority"] is None, m["priority"]))


def differences(saved, current):
    """List of (identity label, field, wanted, found) for monitors present in both."""
    now = {m["id"]: m for m in current}
    diffs = []
    for want in saved:
        have = now.get(want["id"])
        if not have:
            continue
        for field in ("mode", "position", "rotation", "scale", "priority"):
            w, h = want.get(field), have.get(field)
            if field == "scale":
                same = abs(float(w) - float(h)) < 0.001
            else:
                same = w == h
            if not same:
                diffs.append((want["label"], field, w, h))
    return diffs


# ---- applying -------------------------------------------------------------------------------

def plan_args(saved, current, kscreen):
    """kscreen-doctor arguments that bring the present monitors to the saved layout."""
    now = {m["id"]: m for m in current}
    present = [w for w in saved if w["id"] in now]
    if len(present) < 2:
        return []
    modes_by_connector = {o["name"]: {m["name"] for m in o["modes"]} for o in kscreen["outputs"]}
    ranked = sorted(present, key=lambda w: (w.get("priority") is None, w.get("priority")))
    new_priority = {w["id"]: i + 1 for i, w in enumerate(ranked)}
    args = []
    for w in present:
        have = now[w["id"]]
        conn = have["connector"]
        if w.get("mode") and w["mode"] != have["mode"] and w["mode"] in modes_by_connector.get(conn, ()):
            args.append("output.%s.mode.%s" % (conn, w["mode"]))
        if w["rotation"] != have["rotation"]:
            args.append("output.%s.rotation.%s" % (conn, w["rotation"]))
        if abs(float(w["scale"]) - float(have["scale"])) >= 0.001:
            args.append("output.%s.scale.%s" % (conn, ("%g" % w["scale"])))
        if list(w["position"]) != list(have["position"]):
            args.append("output.%s.position.%d,%d" % (conn, w["position"][0], w["position"][1]))
    if any(have_prio != new_priority[w["id"]] for w in present for have_prio in [now[w["id"]]["priority"]]):
        for w in ranked:
            args.append("output.%s.priority.%d" % (now[w["id"]]["connector"], new_priority[w["id"]]))
    return args


def load_layout(path):
    with open(path) as f:
        data = json.load(f)
    return data["monitors"]


def apply_layout(layout_path, drm_root="/sys/class/drm", kscreen_reader=run_kscreen_json, runner=None, dry_run=False, log=print):
    """Re-apply the saved layout if it differs. Returns the list of arguments used (empty if nothing to do)."""
    try:
        saved = load_layout(layout_path)
    except (OSError, ValueError, KeyError):
        log("no saved layout at %s (run: z13-display-restore save)" % layout_path)
        return []
    kscreen = kscreen_reader()
    idmap = connected_monitors(drm_root)
    current = snapshot(kscreen, idmap)
    diffs = differences(saved, current)
    if not diffs:
        log("layout already matches the saved one")
        return []
    args = plan_args(saved, current, kscreen)
    if not args:
        log("fewer than two saved monitors are connected, leaving the layout alone")
        return []
    for label, field, want, have in diffs:
        log("  %s: %s is %s, saved is %s" % (label, field, have, want))
    if dry_run:
        log("would run: kscreen-doctor " + " ".join(args))
        return args
    runner = runner or (lambda a: subprocess.run(["kscreen-doctor"] + a, capture_output=True, text=True, timeout=30, check=False))
    result = runner(args)
    if getattr(result, "returncode", 0) != 0:
        log("kscreen-doctor reported a problem: %s" % (getattr(result, "stderr", "") or "").strip()[:200])
    else:
        log("applied the saved layout to %d monitor(s)" % len({a.split(".")[1] for a in args}))
    return args


def save_layout(layout_path, drm_root="/sys/class/drm", kscreen_reader=run_kscreen_json, log=print):
    kscreen = kscreen_reader()
    current = snapshot(kscreen, connected_monitors(drm_root))
    if not current:
        log("no monitors with a readable EDID found, nothing saved")
        return 1
    os.makedirs(os.path.dirname(layout_path), exist_ok=True)
    with open(layout_path, "w") as f:
        json.dump({"version": 1, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "monitors": current}, f, indent=2)
        f.write("\n")
    for m in current:
        log("saved %-14s %-18s mode=%-14s pos=%s rotation=%s scale=%g priority=%s" % (m["label"][:14], m["id"], m["mode"], m["position"], m["rotation"], m["scale"], m["priority"]))
    log("layout saved to %s" % layout_path)
    return 0


# ---- watching -------------------------------------------------------------------------------

class Watcher:
    """Re-applies the layout after the connected monitors or their connectors change, or after a wake."""

    def __init__(self, read_signature, apply, now=time.monotonic, wake_clock=None, sleep=time.sleep, log=print):
        self.read_signature = read_signature
        self.apply = apply
        self.now = now
        self.wake_clock = wake_clock or (lambda: time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC))
        self.sleep = sleep
        self.log = log
        self.last_signature = None
        self.pending_since = None
        self.last_apply = -MIN_APPLY_GAP
        self.last_wake_offset = self.wake_clock()
        self.started = False

    def step(self):
        t = self.now()
        trigger = None
        sig = self.read_signature()
        offset = self.wake_clock()
        if offset - self.last_wake_offset > WAKE_JUMP_SECONDS:
            self.last_wake_offset = offset
            trigger = "the machine woke up"
        if not self.started:
            self.started = True
            self.last_signature = sig
            trigger = trigger or "starting up"
        elif sig != self.last_signature:
            self.last_signature = sig
            trigger = trigger or "the monitors or their connectors changed"
        if trigger:
            self.log("%s, waiting for things to settle" % trigger)
            self.pending_since = t
            self.pending_signature = sig
        if self.pending_since is not None:
            if sig != self.pending_signature:
                self.pending_since = t
                self.pending_signature = sig
            elif t - self.pending_since >= SETTLE_SECONDS and t - self.last_apply >= MIN_APPLY_GAP:
                self.pending_since = None
                self.last_apply = t
                self.apply()
                return "applied"
        return trigger or "idle"


def watch(layout_path, drm_root="/sys/class/drm", log=print):
    def signature():
        return tuple(sorted((c, i["id"]) for c, i in connected_monitors(drm_root).items()))

    def do_apply():
        try:
            apply_layout(layout_path, drm_root=drm_root, log=log)
        except Exception as e:  # keep the loop alive whatever kscreen-doctor does
            log("apply failed: %s" % e)

    w = Watcher(signature, do_apply, log=log)
    log("watching monitors, layout file %s" % layout_path)
    while True:
        w.step()
        time.sleep(POLL_SECONDS)


# ---- command line ---------------------------------------------------------------------------

def cmd_status(layout_path, drm_root="/sys/class/drm", log=print):
    idmap = connected_monitors(drm_root)
    kscreen = run_kscreen_json()
    current = snapshot(kscreen, idmap)
    log("monitors now:")
    for m in current:
        log("  %-9s %-20s %-24s mode=%-14s pos=%s rot=%s scale=%g prio=%s" % (m["connector"], m["label"][:20], m["id"], m["mode"], m["position"], m["rotation"], m["scale"], m["priority"]))
    try:
        saved = load_layout(layout_path)
    except (OSError, ValueError, KeyError):
        log("no saved layout yet (run: z13-display-restore save)")
        return 0
    diffs = differences(saved, current)
    missing = [w["label"] for w in saved if w["id"] not in {m["id"] for m in current}]
    log("saved layout: %d monitor(s), %s" % (len(saved), "matches" if not diffs else "%d difference(s)" % len(diffs)))
    for label, field, want, have in diffs:
        log("  %s: %s is %s, saved is %s" % (label, field, have, want))
    if missing:
        log("saved but not connected now: %s" % ", ".join(missing))
    return 0


def main(argv=None):
    sys.stdout.reconfigure(line_buffering=True)  # under systemd stdout is a pipe, so lines would otherwise sit in a buffer
    p = argparse.ArgumentParser(description="Keep the monitor layout, matched by monitor identity")
    p.add_argument("--layout", default=DEFAULT_LAYOUT, help="layout file (default: %(default)s)")
    p.add_argument("--drm", default="/sys/class/drm", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("save")
    sub.add_parser("status")
    a = sub.add_parser("apply")
    a.add_argument("--dry-run", action="store_true")
    sub.add_parser("watch")
    args = p.parse_args(argv)
    if args.command == "save":
        return save_layout(args.layout, args.drm)
    if args.command == "status":
        return cmd_status(args.layout, args.drm)
    if args.command == "apply":
        apply_layout(args.layout, args.drm, dry_run=args.dry_run)
        return 0
    watch(args.layout, args.drm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
