#!/usr/bin/env python3
"""Hold the Performance power profile while chosen Steam games run on AC power."""

import argparse
import os
import re
import signal
import sys
import time
from collections import namedtuple

APP_ID = "z13-game-profile"
POWER_BUS = "org.freedesktop.UPower.PowerProfiles"
POWER_PATH = "/org/freedesktop/UPower/PowerProfiles"
POWER_IFACE = "org.freedesktop.UPower.PowerProfiles"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
BUS_DAEMON = "org.freedesktop.DBus"
DEFAULT_PROFILE = "performance"
DEFAULT_POLL_SECONDS = 5
RELEASE_DELAY_SECONDS = 15
CALL_TIMEOUT_MS = 5000
READ_LIMIT = 64 * 1024
DEFAULT_PROC = "/proc"
DEFAULT_POWER = "/sys/class/power_supply"
DEFAULT_CONFIG = "~/.config/z13-game-profile/games.conf"
DEFAULT_GAMES = {"1142710": "Total War: WARHAMMER III", "2420110": "Horizon Forbidden West"}
ENVIRON_COMMS = {"reaper", "wine", "wineserver", "wine64", "proton"}
CONFIG_LINE = re.compile(r"([0-9]+)(?:\s+(.+))?")
Deps = namedtuple("Deps", "client_factory clock scheduler_factory out uid")


class BusError(Exception):
    """A failed or unavailable D-Bus call; str() is the bus's message."""


def emit(msg):
    print(msg, flush=True)


def read_limited(path):
    """Return up to READ_LIMIT bytes of a file, or None when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            return handle.read(READ_LIMIT)
    except OSError:
        return None


def read_text(path):
    data = read_limited(path)
    return None if data is None else data.decode("utf-8", errors="replace").strip()


def split_nul(data):
    if data is None:
        return None
    return [part.decode("utf-8", errors="replace") for part in data.split(b"\0") if part]


def process_dirs(proc_root):
    """Return (pid, path) for each numeric directory in the proc tree."""
    try:
        with os.scandir(proc_root) as entries:
            return [(entry.name, entry.path) for entry in entries
                    if entry.name.isascii() and entry.name.isdigit() and entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def owned_by(path, uid):
    try:
        return os.stat(path).st_uid == uid
    except OSError:
        return False


def app_from_cmdline(args, games):
    if "SteamLaunch" not in args:
        return None
    for arg in args:
        key, sep, value = arg.partition("=")
        if sep and key == "AppId" and value in games:
            return value
    return None


def app_from_environ(entries, games):
    for entry in entries:
        key, sep, value = entry.partition("=")
        if sep and key == "SteamAppId" and value in games:
            return value
    return None


def environ_may_match(comm):
    return comm is not None and (comm in ENVIRON_COMMS or comm.endswith(".exe"))


def find_running_games(games, proc_root, uid):
    """Return {app_id: pid} for each configured game that a process of this user shows."""
    running = {}
    for pid, path in process_dirs(proc_root):
        if not owned_by(path, uid):
            continue
        args = split_nul(read_limited(os.path.join(path, "cmdline")))
        if args is None:
            continue
        app_id = app_from_cmdline(args, games)
        if app_id is None and environ_may_match(read_text(os.path.join(path, "comm"))):
            app_id = app_from_environ(split_nul(read_limited(os.path.join(path, "environ"))) or [], games)
        if app_id is not None:
            running.setdefault(app_id, int(pid))
    return running


def first_running(games, running):
    for app_id in games:
        if app_id in running:
            return app_id
    return None


def on_ac_power(root):
    """Return True on AC: a Mains supply is online, or there is no Mains and no Battery supply."""
    try:
        with os.scandir(root) as entries:
            paths = [entry.path for entry in entries]
    except OSError:
        paths = []
    mains = []
    battery = False
    for path in paths:
        kind = read_text(os.path.join(path, "type"))
        if kind is None:
            continue
        if "Mains" in kind:
            mains.append(read_text(os.path.join(path, "online")) == "1")
        elif "Battery" in kind:
            battery = True
    if mains:
        return any(mains)
    return not battery


def parse_config(text):
    """Return ([(app_id, name or None)], [messages]) for the games file text."""
    entries = []
    messages = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = CONFIG_LINE.fullmatch(line)
        if match is None:
            messages.append(f"skipped games file line {number}: {line}")
            continue
        entries.append((match.group(1), match.group(2)))
    return entries, messages


def load_config(path):
    """Return (entries, messages); a missing file is silent and an unreadable one gives one message."""
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return [], []
    except OSError as err:
        return [], [f"cannot read {path} ({err.strerror}); using the default games"]
    return parse_config(text)


def game_name(app_id):
    return f"Steam app {app_id}"


def merge_games(defaults, entries, extra_ids):
    games = dict(defaults)
    for app_id, name in entries:
        if name is not None:
            games[app_id] = name
        else:
            games.setdefault(app_id, game_name(app_id))
    for app_id in extra_ids:
        games.setdefault(app_id, game_name(app_id))
    return games


def ours_listed(holds):
    """Return True or False for whether ActiveProfileHolds lists our hold, or None for an unexpected shape."""
    if not isinstance(holds, list) or not all(isinstance(entry, dict) for entry in holds):
        return None
    return any(entry.get("ApplicationId") == APP_ID for entry in holds)


def profile_names(profiles):
    """Return the Profile names the daemon lists, or None for an unexpected shape."""
    if not isinstance(profiles, list) or not all(isinstance(entry, dict) for entry in profiles):
        return None
    return [entry["Profile"] for entry in profiles if isinstance(entry.get("Profile"), str)]


class Service:
    """One poll decides whether to hold the profile; the D-Bus client, clock and output are injected."""

    def __init__(self, games, profile, client, clock, out, dry_run, proc_root, power_root, uid):
        self.games = games
        self.profile = profile
        self.client = client
        self.clock = clock
        self.out = out
        self.dry_run = dry_run
        self.proc_root = proc_root
        self.power_root = power_root
        self.uid = uid
        self.held = False
        self.cookie = None
        self.debounce_since = None
        self.problems = {}

    def problem(self, key, message):
        """Print a problem once; the same message is not repeated until a call under that key succeeds."""
        if self.problems.get(key) != message:
            self.problems[key] = message
            self.out(message)

    def clear_problem(self, key):
        self.problems.pop(key, None)

    def poll(self):
        now = self.clock()
        if not self.client.daemon_present():
            self.problem("daemon", "power-profiles daemon is not on the bus")
            self.held = False
            self.cookie = None
            self.debounce_since = None
            return
        self.clear_problem("daemon")
        game = first_running(self.games, find_running_games(self.games, self.proc_root, self.uid))
        on_ac = on_ac_power(self.power_root)
        if self.held and not self.dry_run:
            self.verify_hold()
        if not on_ac:
            self.debounce_since = None
            if self.held:
                self.release("charger unplugged")
            return
        if game is not None:
            self.debounce_since = None
            if not self.held:
                self.take(game)
            return
        if not self.held:
            self.debounce_since = None
            return
        if self.debounce_since is None:
            self.debounce_since = now
        if now - self.debounce_since >= RELEASE_DELAY_SECONDS:
            self.release("game exited")

    def verify_hold(self):
        try:
            holds = self.client.read_holds()
        except BusError as err:
            self.problem("holds", f"cannot read ActiveProfileHolds: {err}")
            return
        listed = ours_listed(holds)
        if listed is None:
            self.problem("holds", "ActiveProfileHolds has an unexpected shape")
            return
        self.clear_problem("holds")
        if not listed:
            self.out("hold no longer listed in ActiveProfileHolds")
            self.held = False
            self.cookie = None

    def take(self, app_id):
        name = self.games[app_id]
        try:
            names = profile_names(self.client.read_profiles())
        except BusError as err:
            self.problem("profiles", f"cannot read Profiles: {err}")
            return
        if names is None:
            self.problem("profiles", "Profiles has an unexpected shape")
            return
        self.clear_problem("profiles")
        if self.profile not in names:
            self.problem("profile", f"profile {self.profile} is not listed by the daemon; nothing to hold")
            return
        self.clear_problem("profile")
        if self.dry_run:
            self.held = True
            self.out(f"would hold: {self.profile} for {name}")
            return
        try:
            cookie = self.client.hold(self.profile, f"{name} is running")
        except BusError as err:
            self.problem("hold", f"HoldProfile failed: {err}")
            return
        self.clear_problem("hold")
        self.held = True
        self.cookie = cookie
        self.out(f"hold taken: {self.profile} for {name}")

    def release(self, reason):
        if self.dry_run:
            self.out(f"would release: {reason}")
        else:
            try:
                self.client.release(self.cookie)
            except BusError as err:
                self.problem("release", f"ReleaseProfile failed: {err}")
                return
            self.clear_problem("release")
            self.out(f"hold released: {reason}")
        self.held = False
        self.cookie = None
        self.debounce_since = None

    def stop(self):
        if self.held:
            self.release("service stopping")


def describe_hold(entry):
    if not isinstance(entry, dict):
        return f"unexpected entry: {entry!r}"
    return f"{entry.get('Profile', '?')} from {entry.get('ApplicationId', '?')} ({entry.get('Reason', 'no reason')})"


def print_holds(holds, out):
    if not isinstance(holds, list):
        out("holds: unexpected shape")
    elif not holds:
        out("holds: none")
    else:
        out("holds:")
        for entry in holds:
            out(f"  {describe_hold(entry)}")


def describe_next(profile, names, on_ac, game_name_or_none, ours):
    if names is None:
        return "nothing, Profiles is unreadable"
    if profile not in names:
        return f"nothing, {profile} is not listed by the daemon"
    if not on_ac:
        return "release, charger unplugged" if ours else "nothing, on battery"
    if game_name_or_none is not None:
        return f"keep the hold for {game_name_or_none}" if ours else f"take a hold for {game_name_or_none}"
    if ours:
        return f"release after the {RELEASE_DELAY_SECONDS} s delay, no game running"
    return "nothing, no game running"


def run_status(games, profile, proc_root, power_root, uid, client, out):
    """Print one detection pass and what the service would do now; it never takes a hold."""
    running = find_running_games(games, proc_root, uid)
    game = first_running(games, running)
    on_ac = on_ac_power(power_root)
    out("configured games:")
    for app_id, name in games.items():
        out(f"  {app_id} {name}")
    out("running games:")
    if not running:
        out("  none")
    for app_id, pid in running.items():
        out(f"  {games[app_id]} (pid {pid})")
    out(f"on AC: {'yes' if on_ac else 'no'}")
    if not client.daemon_present():
        out("daemon not on the bus")
        out("would do: nothing until the daemon is on the bus")
        return 0
    try:
        out(f"active profile: {client.read_active_profile()}")
    except BusError as err:
        out(f"active profile: unreadable ({err})")
    try:
        holds = client.read_holds()
    except BusError as err:
        out(f"holds: unreadable ({err})")
        holds = None
    else:
        print_holds(holds, out)
    try:
        names = profile_names(client.read_profiles())
    except BusError:
        names = None
    ours = ours_listed(holds)
    if ours is None:
        out("would do: unknown until the holds can be read")
        return 0
    game_name_or_none = games[game] if game is not None else None
    out("would do: " + describe_next(profile, names, on_ac, game_name_or_none, ours))
    return 0


class PowerProfilesClient:
    """The system-bus client; one connection is kept for the life of the process, because a hold ends when its caller disconnects."""

    def __init__(self):
        self._connection = None

    def _call(self, bus_name, path, interface, method, signature, args, reply_type):
        from gi.repository import Gio, GLib
        try:
            if self._connection is None:
                self._connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            reply = self._connection.call_sync(
                bus_name, path, interface, method, GLib.Variant(signature, args),
                GLib.VariantType.new(reply_type) if reply_type else None,
                Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None)
        except GLib.Error as err:
            raise BusError(err.message) from None
        return reply.unpack()

    def _get(self, name):
        (value,) = self._call(POWER_BUS, POWER_PATH, PROPERTIES_IFACE, "Get", "(ss)", (POWER_IFACE, name), "(v)")
        return value

    def daemon_present(self):
        try:
            (present,) = self._call(BUS_DAEMON, "/org/freedesktop/DBus", BUS_DAEMON, "NameHasOwner",
                                    "(s)", (POWER_BUS,), "(b)")
        except BusError:
            return False
        return bool(present)

    def hold(self, profile, reason):
        (cookie,) = self._call(POWER_BUS, POWER_PATH, POWER_IFACE, "HoldProfile",
                               "(sss)", (profile, reason, APP_ID), "(u)")
        return cookie

    def release(self, cookie):
        self._call(POWER_BUS, POWER_PATH, POWER_IFACE, "ReleaseProfile", "(u)", (cookie,), None)

    def read_holds(self):
        return self._get("ActiveProfileHolds")

    def read_profiles(self):
        return self._get("Profiles")

    def read_active_profile(self):
        return self._get("ActiveProfile")


class GLibScheduler:
    """Runs polls on a GLib main loop; SIGTERM and SIGINT end the loop so the caller can release the hold."""

    def __init__(self):
        from gi.repository import GLib, GLibUnix
        self._glib = GLib
        self._unix = GLibUnix
        self._loop = GLib.MainLoop()

    def repeat(self, seconds, callback):
        self._glib.timeout_add_seconds(seconds, self._tick, callback)

    def _tick(self, callback):
        callback()
        return True

    def _quit(self):
        self._loop.quit()
        return False

    def run(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._unix.signal_add(self._glib.PRIORITY_DEFAULT, signum, self._quit)
        self._loop.run()


def app_id_arg(text):
    if not re.fullmatch(r"[0-9]+", text):
        raise argparse.ArgumentTypeError(f"not a Steam app id: {text!r}")
    return text


def positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1: {text}")
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        description="Hold the Performance power profile while chosen Steam games run on AC power.")
    parser.add_argument("--app-id", action="append", type=app_id_arg, metavar="ID",
                        help="add a Steam app id (repeatable)")
    parser.add_argument("--config", metavar="PATH",
                        help="games file (default ~/.config/z13-game-profile/games.conf)")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="power profile to hold (default %(default)s)")
    parser.add_argument("--poll-seconds", type=positive_int, default=DEFAULT_POLL_SECONDS,
                        help="seconds between checks (default %(default)s)")
    parser.add_argument("--proc-root", default=DEFAULT_PROC, help="proc tree to scan (default %(default)s)")
    parser.add_argument("--power-supply-root", default=DEFAULT_POWER,
                        help="power supply tree (default %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="log what would change, never call HoldProfile or ReleaseProfile")
    parser.add_argument("--status", action="store_true",
                        help="print one detection pass and what the service would do, then exit")
    return parser


def default_deps():
    return Deps(PowerProfilesClient, time.monotonic, GLibScheduler, emit, os.getuid())


def main(argv=None, deps=None):
    deps = deps or default_deps()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config is not None and not os.path.exists(os.path.expanduser(args.config)):
        parser.error(f"config file not found: {args.config}")
    entries, messages = load_config(os.path.expanduser(args.config or DEFAULT_CONFIG))
    for message in messages:
        deps.out(message)
    games = merge_games(DEFAULT_GAMES, entries, args.app_id or [])
    client = deps.client_factory()
    if args.status:
        return run_status(games, args.profile, args.proc_root, args.power_supply_root, deps.uid, client, deps.out)
    service = Service(games, args.profile, client, deps.clock, deps.out, args.dry_run,
                      args.proc_root, args.power_supply_root, deps.uid)
    scheduler = deps.scheduler_factory()
    try:
        service.poll()
        scheduler.repeat(args.poll_seconds, service.poll)
        scheduler.run()
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
