#!/usr/bin/env python3
"""Suspend after idle when only Claude Desktop's rotation claims block sleep, and hold off while Claude shows real activity."""

import argparse
import bisect
import datetime
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import time
from collections import namedtuple

IDLE_SECONDS = 3600
POLL_SECONDS = 60
GRACE_SECONDS = 600
SUSPEND_COOLDOWN_SECONDS = 180
RESUME_JUMP_SECONDS = 5
ROTATION_TIMEOUT_SECONDS = 330
ROTATION_MIN_MS = 299000
ROTATION_MAX_MS = 302000
PAIR_WINDOW_SECONDS = 2
LOG_CAP_BYTES = 64 * 1024 * 1024
LOG_READ_ATTEMPTS = 3
DEFAULT_LOG = "~/.config/Claude/logs/main.log"
CLAUDE_APP = "/usr/bin/claude-desktop"
SWAYIDLE_HINT = "sudo pacman -S --needed swayidle"
INHIBIT_CMD = [
    "busctl", "--user", "--json=short", "get-property",
    "org.kde.Solid.PowerManagement.PolicyAgent", "/org/kde/Solid/PowerManagement/PolicyAgent",
    "org.kde.Solid.PowerManagement.PolicyAgent", "ActiveInhibitions",
]
SUSPEND_CMD = [
    "busctl", "--user", "call", "org.kde.Solid.PowerManagement", "/org/freedesktop/PowerManagement",
    "org.freedesktop.PowerManagement", "Suspend",
]

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[\w+\] (.*)$")
ROTATION_DUE = "[remote-tools-device] rotation due — re-presenting credential in-band"
RT_HELD = "[remote-tools-device] keep-awake held"
RT_RELEASED = "[remote-tools-device] keep-awake released"
RT_QUIET_RELEASE_RE = re.compile(r"\[remote-tools-device\] keep-awake released \(quiet, held (\d+)ms\)")
RT_STARTED_RE = re.compile(r"\[keep-awake\] started \(id=\d+, claims=remote_tools\)")
RT_STOPPED_RE = re.compile(r"\[keep-awake\] stopped \(id=\d+\): last claim remote_tools released")
SERVED_RE = re.compile(r"\[remote-tools-device\] served (\S+)")
STARTED_RE = re.compile(r"\[keep-awake\] started \(id=(\d+), claims=([^)]*)\)")
STOPPED_RE = re.compile(r"\[keep-awake\] stopped \(id=(\d+)\)")
LAUNCH = "Starting app {"
CODE_TAKEN = "[keep-awake] code session hold taken"
CODE_RELEASED = "[keep-awake] code session hold released"
MANUAL_TAKEN_RE = re.compile(r"\[keep-awake\] session (\S+) manual hold taken")
MANUAL_RELEASED_RE = re.compile(r"\[keep-awake\] session (\S+) manual hold released")

Inhibition = namedtuple("Inhibition", "what app reason mode flags")
Activity = namedtuple("Activity", "last_real open_holds history_ok")
Clocks = namedtuple("Clocks", "now boot mono")
Deps = namedtuple("Deps", "run spawn which clocks selector_factory out reader")
READ_BYTES = 4096


def emit(msg):
    print(msg, flush=True)


def boot_clock():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def pipe_reader(stream):
    """Return a read function that returns the bytes the pipe holds now, and b"" at end of output."""
    return lambda: os.read(stream.fileno(), READ_BYTES)


def default_deps():
    return Deps(subprocess.run, subprocess.Popen, shutil.which,
                Clocks(time.time, boot_clock, time.monotonic),
                selectors.DefaultSelector, emit, pipe_reader)


def update_grace(prev_delta, delta, now, grace_until):
    """Start a grace period when boot-minus-monotonic grew, which means the machine slept."""
    if prev_delta is not None and delta - prev_delta > RESUME_JUMP_SECONDS:
        return now + GRACE_SECONDS
    return grace_until


def parse_inhibitions(text):
    """Return the ActiveInhibitions rows as Inhibition tuples, or None when the output is malformed."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("type") != "a(ssssu)" or not isinstance(doc.get("data"), list):
        return None
    rows = []
    for row in doc["data"]:
        if not isinstance(row, list) or len(row) != 5:
            return None
        if not all(isinstance(field, str) for field in row[:4]):
            return None
        if not isinstance(row[4], int) or isinstance(row[4], bool):
            return None
        rows.append(Inhibition(*row))
    return rows


def read_inhibitions(run):
    """Return the active inhibitions, or None when busctl fails or its output is unusable."""
    try:
        result = run(INHIBIT_CMD, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_inhibitions(result.stdout)


def parse_record(line):
    """Return (timestamp, text) for a log line; the timestamp is None when it does not parse."""
    match = LINE_RE.match(line)
    if match is None:
        return None, line
    try:
        stamp = datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None, line
    return stamp, match.group(2)


def is_keep_awake(text):
    return "[keep-awake]" in text or "[remote-tools-device] keep-awake" in text


def served_tool(text):
    match = SERVED_RE.search(text)
    return match.group(1) if match else None


def near(records, indices, when):
    return {k for k in indices if abs(records[k][0] - when) <= PAIR_WINDOW_SECONDS}


def rotation_lines(records, now, launches):
    """Return (ignored, extra): indices of rotation-interval lines, and activity times for holds past the timeout; a launch after an ongoing hold forgets it."""
    dues = sorted(ts for ts, text in records if ts is not None and ROTATION_DUE in text)
    held = [i for i, (ts, text) in enumerate(records) if ts is not None and RT_HELD in text]
    released = [i for i, (ts, text) in enumerate(records) if ts is not None and RT_RELEASED in text]
    starts = [i for i, (ts, text) in enumerate(records) if ts is not None and RT_STARTED_RE.search(text)]
    stops = [i for i, (ts, text) in enumerate(records) if ts is not None and RT_STOPPED_RE.search(text)]
    ignored = set()
    extra = []
    for n, i in enumerate(held):
        start = records[i][0]
        due = bisect.bisect_left(dues, start - PAIR_WINDOW_SECONDS)
        if due == len(dues) or dues[due] > start:
            continue
        next_held = held[n + 1] if n + 1 < len(held) else len(records)
        pos = bisect.bisect_right(released, i)
        release = released[pos] if pos < len(released) else None
        if release is None or release > next_held:
            voided = bisect.bisect_right(launches, i) < len(launches)
            if voided or now - start <= ROTATION_TIMEOUT_SECONDS:
                ignored.add(i)
                ignored |= near(records, starts, start)
            else:
                extra.append(now)
            continue
        match = RT_QUIET_RELEASE_RE.search(records[release][1])
        if match is None or not ROTATION_MIN_MS <= int(match.group(1)) <= ROTATION_MAX_MS:
            continue
        ignored |= {i, release} | near(records, starts, start) | near(records, stops, records[release][0])
    return ignored, extra


def track_hold(holds, text):
    """Open or close holds; ids are ignored, a blocker start opens one unless it is only remote_tools, and a stop clears every hold."""
    started = STARTED_RE.search(text)
    if started:
        claims = started.group(2)
        if any(part.strip() != "remote_tools" for part in claims.split(",")):
            holds["blocker"] = claims.strip() or "unknown claims"
        return
    manual_taken = MANUAL_TAKEN_RE.search(text)
    manual_released = MANUAL_RELEASED_RE.search(text)
    if STOPPED_RE.search(text):
        holds.clear()
    elif CODE_TAKEN in text:
        holds["code session"] = "code session"
    elif CODE_RELEASED in text:
        holds.pop("code session", None)
    elif manual_taken:
        holds["session " + manual_taken.group(1)] = "manual hold"
    elif manual_released:
        holds.pop("session " + manual_released.group(1), None)


def analyse(lines, now, idle_seconds, read_ok=True):
    """Return the Activity for Claude log lines; read_ok False means a log file was missing, unreadable or too large."""
    records = [parse_record(line) for line in lines]
    stamps = [ts for ts, _ in records if ts is not None]
    history_ok = bool(read_ok and stamps and min(stamps) <= now - idle_seconds)
    launches = [i for i, (ts, text) in enumerate(records) if ts is not None and text.startswith(LAUNCH)]
    ignored, extra = rotation_lines(records, now, launches)
    holds = {}
    times = list(extra)
    for index, (ts, text) in enumerate(records):
        if ts is not None and text.startswith(LAUNCH):
            holds.clear()
            continue
        if ts is not None:
            track_hold(holds, text)
        if index in ignored:
            continue
        tool = served_tool(text)
        if is_keep_awake(text) or (tool is not None and tool != "get_device_info"):
            times.append(now if ts is None else ts)
    last_real = max(times) if times else None
    return Activity(last_real, list(dict.fromkeys(holds.values())), history_ok)


def decide(now, idle, grace_until, inhibitions, activity, idle_seconds, cooldown_until=0.0):
    """Return (suspend, reason) for one evaluation, checking in the order the plan lists with the suspend cooldown after grace."""
    if not idle:
        return False, "not idle"
    if now < grace_until:
        return False, "grace period after resume"
    if now < cooldown_until:
        return False, "suspend already requested"
    if inhibitions is None:
        return False, "inhibitions unreadable"
    if not inhibitions:
        return False, "no inhibitions (PowerDevil handles sleep)"
    for row in inhibitions:
        if row.app != CLAUDE_APP:
            return False, f"blocked by {row.app}"
    if activity is None or not activity.history_ok:
        return False, "Claude log history too short"
    if activity.open_holds:
        return False, "Claude hold in progress: " + ", ".join(activity.open_holds)
    if activity.last_real is not None and now - activity.last_real < idle_seconds:
        return False, f"Claude activity {int(max(0, now - activity.last_real))}s ago"
    return True, f"only Claude rotation claims for {idle_seconds}s"


def sibling_log_path(path):
    root, ext = os.path.splitext(path)
    return root + "1" + ext


def stat_or_none(path):
    try:
        return os.stat(path)
    except FileNotFoundError:
        return None


def identities(paths):
    return tuple(None if st is None else st.st_ino for st in map(stat_or_none, paths))


def read_lines(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", errors="replace").splitlines()


def read_claude_logs(path, cap=LOG_CAP_BYTES, attempts=LOG_READ_ATTEMPTS):
    """Return (lines, files, ok): the rotated sibling then the main log; ok is False when the main log is missing, a file is unreadable or too large, or a rotation happened during the read."""
    paths = (sibling_log_path(path), path)
    files = []
    try:
        for _ in range(attempts):
            before = identities(paths)
            stats = [stat_or_none(p) for p in paths]
            files = [(p, None if st is None else st.st_size) for p, st in zip(paths, stats)]
            if stats[1] is None or sum(st.st_size for st in stats if st is not None) > cap:
                return [], files, False
            lines = []
            for p, st in zip(paths, stats):
                if st is not None:
                    lines.extend(read_lines(p))
            if identities(paths) == before:
                return lines, files, True
    except OSError:
        return [], files, False
    return [], files, False


def read_activity(log_path, now, idle_seconds):
    lines, _files, ok = read_claude_logs(log_path)
    return analyse(lines, now, idle_seconds, ok)


def call_suspend(run, out):
    """Ask PowerDevil to suspend; a failure is logged and returns False."""
    try:
        result = run(SUSPEND_CMD, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        out(f"suspend failed: {err}")
        return False
    if result.returncode != 0:
        out(f"suspend failed: busctl exited {result.returncode}: {(result.stderr or '').strip()}")
        return False
    return True


class Monitor:
    def __init__(self, idle_seconds, log_path, deps, dry_run=False):
        self.idle_seconds = idle_seconds
        self.log_path = log_path
        self.deps = deps
        self.dry_run = dry_run
        self.idle = False
        self.grace_until = 0.0
        self.cooldown_until = 0.0
        self.prev_delta = None
        self.last_reason = None

    def on_line(self, line):
        if line == "IDLE":
            self.idle = True
        elif line == "RESUME":
            self.idle = False

    def evaluate(self):
        clocks = self.deps.clocks
        now = clocks.now()
        delta = clocks.boot() - clocks.mono()
        self.grace_until = update_grace(self.prev_delta, delta, now, self.grace_until)
        self.prev_delta = delta
        inhibitions = read_inhibitions(self.deps.run) if self.idle else None
        activity = None
        if self.idle and now >= self.grace_until and now >= self.cooldown_until and inhibitions and all(r.app == CLAUDE_APP for r in inhibitions):
            activity = read_activity(self.log_path, now, self.idle_seconds)
        suspend_now, reason = decide(now, self.idle, self.grace_until, inhibitions, activity, self.idle_seconds,
                                     self.cooldown_until)
        if reason != self.last_reason:
            self.deps.out(reason)
            self.last_reason = reason
        if not suspend_now:
            return
        if self.dry_run:
            self.deps.out(f"would suspend: {reason}")
        else:
            self.deps.out(f"suspend: {reason}")
            call_suspend(self.deps.run, self.deps.out)
            self.cooldown_until = now + SUSPEND_COOLDOWN_SECONDS


class LineSplitter:
    """Split raw swayidle output into complete lines; a partial trailing line waits for the next chunk."""

    def __init__(self):
        self.partial = b""

    def feed(self, chunk):
        *complete, self.partial = (self.partial + chunk).split(b"\n")
        return [text.decode("utf-8", errors="replace").strip() for text in complete]


def serve(monitor, read_chunk, selector, poll_seconds):
    """Evaluate after each wake, once every complete swayidle line in it is applied; return 1 when swayidle closes its output."""
    splitter = LineSplitter()
    while True:
        if selector.select(poll_seconds):
            chunk = read_chunk()
            if chunk == b"":
                return 1
            for line in splitter.feed(chunk):
                monitor.on_line(line)
        monitor.evaluate()


def explain(log_path, idle_seconds, deps):
    """Print the inputs and one decision, assuming idle with no grace period; never spawns swayidle or suspends."""
    now = deps.clocks.now()
    lines, files, read_ok = read_claude_logs(log_path)
    activity = analyse(lines, now, idle_seconds, read_ok)
    inhibitions = read_inhibitions(deps.run)
    suspend_now, reason = decide(now, True, 0.0, inhibitions, activity, idle_seconds)
    deps.out("assumes idle and no grace period")
    deps.out("log files:")
    for path, size in files:
        deps.out(f"  {path}: {'missing' if size is None else str(size) + ' bytes'}")
    if inhibitions is None:
        deps.out("inhibitions: unreadable")
    elif not inhibitions:
        deps.out("inhibitions: none")
    else:
        deps.out("inhibitions: " + ", ".join(row.app for row in inhibitions))
    if activity.last_real is None:
        deps.out("last real activity: none in log")
    else:
        deps.out(f"last real activity: {int(max(0, now - activity.last_real))}s ago")
    deps.out("open holds: " + (", ".join(activity.open_holds) if activity.open_holds else "none"))
    deps.out("history ok: " + ("yes" if activity.history_ok else "no"))
    verdict = "would suspend" if suspend_now else "would not suspend"
    deps.out(f"decision: {verdict}: {reason}")
    return 0


def positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1: {text}")
    return value


def build_parser():
    parser = argparse.ArgumentParser(description="Suspend after idle when only Claude Desktop's rotation claims block sleep.")
    parser.add_argument("--idle-seconds", type=positive_int, default=IDLE_SECONDS,
                        help="idle time before suspend (default %(default)s)")
    parser.add_argument("--poll-seconds", type=positive_int, default=POLL_SECONDS,
                        help="evaluation interval in seconds (default %(default)s)")
    parser.add_argument("--log", help="Claude main.log path (default ~/.config/Claude/logs/main.log)")
    parser.add_argument("--dry-run", action="store_true", help="log what would suspend, but never suspend")
    parser.add_argument("--explain", action="store_true", help="print the inputs and one decision, then exit")
    return parser


def main(argv=None, deps=None):
    deps = deps or default_deps()
    args = build_parser().parse_args(argv)
    log_path = os.path.expanduser(args.log or DEFAULT_LOG)
    if args.explain:
        return explain(log_path, args.idle_seconds, deps)
    if deps.which("swayidle") is None:
        deps.out(f"swayidle is not installed; install it with: {SWAYIDLE_HINT}")
        return 2
    proc = deps.spawn(["swayidle", "timeout", str(args.idle_seconds), "echo IDLE", "resume", "echo RESUME"],
                      stdout=subprocess.PIPE, bufsize=0)
    selector = deps.selector_factory()
    selector.register(proc.stdout, selectors.EVENT_READ)
    monitor = Monitor(args.idle_seconds, log_path, deps, args.dry_run)
    try:
        return serve(monitor, deps.reader(proc.stdout), selector, args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
