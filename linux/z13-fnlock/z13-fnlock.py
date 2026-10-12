#!/usr/bin/env python3
"""Fn lock for the ASUS ROG Flow Z13 (GZ302EA) folio keyboard, USB 0b05:1a30.

The kernel's hid-asus driver sees Fn+Esc as KEY_FN_ESC but only toggles the keyboard's Fn lock for
devices flagged QUIRK_HID_FN_LOCK, and the Z13 folio is not flagged. This sends the same 64-byte HID
feature report the driver sends for the flagged keyboards: report id 0x5a, then d0 4e, then 1 or 0.
Remove this once the kernel does it. See notes/upstream-z13-fnlock-patch.md in the z13-tools repo.

Fn lock ON means F1 to F12 are plain F-keys. Fn lock OFF means the row sends media keys.

  z13-fnlock find                      list the keyboard's input and hidraw nodes (no root, sends nothing)
  sudo z13-fnlock on | off             set the state once
  sudo z13-fnlock daemon [--default on|off]
                                       toggle on Fn+Esc, re-apply when the keyboard reappears
                                       or when the daemon gets SIGUSR1 (used after resume)
"""
import argparse
import errno
import fcntl
import glob
import os
import select
import signal
import struct
import sys
import time

VENDOR = 0x0B05
PRODUCT = 0x1A30
KEY_FN_ESC = 465
EV_KEY = 1
REPORT_ID = 0x5A
REPORT_SIZE = 64
EVENT = struct.Struct("llHHi")
RESCAN_SECONDS = 2.0
DEBOUNCE_SECONDS = 0.3


def read_sys(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def key_bit_set(bitmap, code):
    """Test a key code in a sysfs capabilities/key string (64-bit words, most significant first)."""
    words = bitmap.split()
    index = code // 64
    if index >= len(words):
        return False
    return bool(int(words[-1 - index], 16) >> (code % 64) & 1)


def find_input_nodes():
    """Input event nodes of the Z13 folio that can send KEY_FN_ESC."""
    nodes = []
    for path in sorted(glob.glob("/sys/class/input/event*")):
        dev = os.path.join(path, "device")
        vid = read_sys(os.path.join(dev, "id", "vendor"))
        pid = read_sys(os.path.join(dev, "id", "product"))
        if vid is None or pid is None or int(vid, 16) != VENDOR or int(pid, 16) != PRODUCT:
            continue
        caps = read_sys(os.path.join(dev, "capabilities", "key"))
        if caps and key_bit_set(caps, KEY_FN_ESC):
            nodes.append("/dev/input/" + os.path.basename(path))
    return nodes


def find_hidraw_nodes():
    """hidraw nodes of the Z13 folio, with whether each carries feature report 0x5a on vendor page 0xff31."""
    hid_id = "HID_ID=0003:%08X:%08X" % (VENDOR, PRODUCT)
    found = []
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.join(path, "device")
        uevent = read_sys(os.path.join(dev, "uevent"))
        if uevent is None or hid_id not in uevent:
            continue
        try:
            with open(os.path.join(dev, "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        found.append({
            "node": "/dev/" + os.path.basename(path),
            "has_report_5a": b"\x85\x5a" in desc,
            "vendor_page_ff31": b"\x06\x31\xff" in desc,
        })
    return found


def find_hidraw():
    good = [c["node"] for c in find_hidraw_nodes() if c["has_report_5a"] and c["vendor_page_ff31"]]
    return good[0] if len(good) == 1 else None


def hidiocsfeature(length):
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x06


def send_fn_lock(enabled, node=None):
    """Send the Fn lock feature report. Returns the hidraw node used."""
    node = node or find_hidraw()
    if node is None:
        raise RuntimeError("no single Z13 folio vendor interface found")
    buf = bytearray(REPORT_SIZE)
    buf[0:4] = bytes([REPORT_ID, 0xD0, 0x4E, 1 if enabled else 0])
    fd = os.open(node, os.O_RDWR)
    try:
        fcntl.ioctl(fd, hidiocsfeature(REPORT_SIZE), buf)
    finally:
        os.close(fd)
    return node


def open_input(path):
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


def log(message):
    print(message, flush=True)


def describe(enabled):
    return "ON (F-keys)" if enabled else "OFF (media keys)"


class Daemon:
    def __init__(self, default_enabled, send=send_fn_lock, find_inputs=find_input_nodes,
                 opener=open_input, logger=log, clock=time.monotonic):
        self.enabled = default_enabled
        self.send = send
        self.find_inputs = find_inputs
        self.opener = opener
        self.log = logger
        self.clock = clock
        self.fds = {}
        self.reapply = True
        self.apply_failed = False
        self.last_press = None

    def drop(self, fd):
        path = self.fds.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass
        if path:
            self.log("keyboard input gone: " + path)

    def sync_devices(self):
        current = set(self.find_inputs())
        for fd, path in list(self.fds.items()):
            if path not in current:
                self.drop(fd)
        known = set(self.fds.values())
        for path in sorted(current - known):
            try:
                fd = self.opener(path)
            except OSError as e:
                self.log("cannot open %s: %s" % (path, e))
                continue
            self.fds[fd] = path
            self.reapply = True
            self.log("keyboard input found: " + path)

    def apply(self):
        try:
            node = self.send(self.enabled)
        except (OSError, RuntimeError) as e:
            if not self.apply_failed:
                self.log("could not set Fn lock: %s; will retry" % e)
                self.apply_failed = True
            return
        self.apply_failed = False
        self.reapply = False
        self.log("Fn lock %s sent to %s" % (describe(self.enabled), node))

    def read(self, fd):
        try:
            data = os.read(fd, EVENT.size * 32)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EINTR):
                return
            self.drop(fd)
            return
        if not data:
            self.drop(fd)
            return
        for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _sec, _usec, etype, code, value = EVENT.unpack_from(data, offset)
            if etype != EV_KEY or code != KEY_FN_ESC or value != 1:
                continue
            now = self.clock()
            if self.last_press is not None and now - self.last_press < DEBOUNCE_SECONDS:
                continue
            self.last_press = now
            self.enabled = not self.enabled
            self.reapply = True
            self.log("Fn+Esc pressed, Fn lock now " + describe(self.enabled))

    def step(self, timeout):
        self.sync_devices()
        if self.reapply and self.fds:
            self.apply()
        if not self.fds:
            time.sleep(timeout)
            return
        ready, _, _ = select.select(list(self.fds), [], [], timeout)
        for fd in ready:
            self.read(fd)

    def run(self):
        self.log("z13-fnlock started, default Fn lock " + describe(self.enabled))
        while True:
            self.step(RESCAN_SECONDS)


def cmd_find(_args):
    inputs = find_input_nodes()
    print("input nodes that can send KEY_FN_ESC: " + (", ".join(inputs) or "none"))
    print("hidraw nodes:")
    for c in find_hidraw_nodes():
        print("  %s  report 0x5a: %s  vendor page 0xff31: %s" % (c["node"], c["has_report_5a"], c["vendor_page_ff31"]))
    target = find_hidraw()
    print("would send to: " + (target or "nothing (no single match)"))
    return 0 if inputs and target else 1


def cmd_set(args):
    try:
        node = send_fn_lock(args.command == "on")
    except PermissionError:
        print("permission denied, run with sudo", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as e:
        print("failed: %s" % e, file=sys.stderr)
        return 1
    print("Fn lock %s sent to %s" % (describe(args.command == "on"), node))
    return 0


def cmd_daemon(args):
    daemon = Daemon(args.default == "on")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGUSR1, lambda *_: setattr(daemon, "reapply", True))
    daemon.run()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fn lock for the Z13 folio keyboard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("find", help="list the keyboard nodes, sends nothing")
    sub.add_parser("on", help="Fn lock ON: F1 to F12 are plain F-keys")
    sub.add_parser("off", help="Fn lock OFF: the row sends media keys")
    d = sub.add_parser("daemon", help="toggle on Fn+Esc and re-apply after reattach or resume")
    d.add_argument("--default", choices=("on", "off"), default="off", help="state at start and after reattach (default: off, media keys)")
    args = parser.parse_args(argv)
    if args.command == "find":
        return cmd_find(args)
    if args.command in ("on", "off"):
        return cmd_set(args)
    return cmd_daemon(args)


if __name__ == "__main__":
    sys.exit(main())
