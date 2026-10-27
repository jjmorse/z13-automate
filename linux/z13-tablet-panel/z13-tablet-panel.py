#!/usr/bin/env python3
"""Make the laptop's Plasma panel taller for touch while KWin is in tablet mode and the laptop is the only screen, and put the saved height back otherwise."""

import argparse
import configparser
import fnmatch
import json
import os
import re
import signal
import sys
from collections import namedtuple

KWIN = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin"
TABLET_IFACE = "org.kde.KWin.TabletModeManager"
PLASMASHELL = "org.kde.plasmashell"
PLASMASHELL_PATH = "/PlasmaShell"
PLASMASHELL_IFACE = "org.kde.PlasmaShell"
PROPS = "org.freedesktop.DBus.Properties"
BUS_DAEMON = "org.freedesktop.DBus"
BUS_PATH = "/org/freedesktop/DBus"
CALL_TIMEOUT_MS = 5000
DEFAULT_TABLET_HEIGHT = 48
DEFAULT_POLL_SECONDS = 10
SETTLE_SECONDS = 3
MIN_HEIGHT = 24
MAX_HEIGHT = 200
DEFAULT_DRM_ROOT = "/sys/class/drm"
DEFAULT_APPLETSRC = "~/.config/plasma-org.kde.plasma.desktop-appletsrc"
DEFAULT_STATE_FILE = "~/.local/state/z13-tablet-panel/state.json"
PANEL_PLUGIN = "org.kde.panel"
PANEL_SECTION = re.compile(r"Containments\]\[([0-9]+)")
CONNECTOR_PATTERN = "card*-*"
HEIGHT_TEXT = re.compile(r"[0-9]+")
Config = namedtuple("Config", "tablet_height appletsrc state_file drm_root poll_seconds dry_run")
State = namedtuple("State", "panel_id original_height")
Deps = namedtuple("Deps", "bus_factory scheduler_factory out")


class BusError(Exception):
    """A failed or unavailable D-Bus call; str() is the bus's message."""


class PanelError(Exception):
    """plasmashell answered with something other than a usable height, or a write did not take."""


def emit(msg):
    print(msg, flush=True)


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def read_text(path):
    """Return the stripped text of a small file, or None when it cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def read_appletsrc(path):
    """Return the text of the Plasma applet config; OSError when it cannot be read."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def count_connected(drm_root):
    """Return how many card connector directories under drm_root have a status of connected."""
    try:
        with os.scandir(drm_root) as entries:
            paths = [entry.path for entry in entries
                     if fnmatch.fnmatchcase(entry.name, CONNECTOR_PATTERN) and entry.is_dir()]
    except OSError:
        return 0
    return sum(1 for path in paths if read_text(os.path.join(path, "status")) == "connected")


def read_script(panel_id):
    """Return the one read form: print the height of the panel with this id."""
    if not is_int(panel_id):
        raise ValueError(f"panel id must be an integer: {panel_id!r}")
    return f"var p=panelById({panel_id}); print(p.height)"


def write_script(panel_id, height):
    """Return the one write form: set the height of the panel with this id."""
    if not is_int(panel_id):
        raise ValueError(f"panel id must be an integer: {panel_id!r}")
    if not is_int(height) or not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise ValueError(f"height must be an integer from {MIN_HEIGHT} to {MAX_HEIGHT}: {height!r}")
    return f"panelById({panel_id}).height = {height}"


def parse_height(text):
    """Return the height plasmashell printed, or raise PanelError unless it is a whole number in range."""
    value = (text or "").strip()
    if HEIGHT_TEXT.fullmatch(value) is None:
        raise PanelError(f"plasmashell printed {text!r}, not a height")
    height = int(value)
    if not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise PanelError(f"height {height} is outside {MIN_HEIGHT} to {MAX_HEIGHT}")
    return height


def read_height(bus, panel_id):
    """Return the panel's height as plasmashell reports it; BusError or PanelError when that fails."""
    return parse_height(bus.evaluate(read_script(panel_id)))


def write_and_verify(bus, panel_id, height):
    """Set the panel's height and read it back; BusError or PanelError when the write or the check fails."""
    bus.evaluate(write_script(panel_id, height))
    got = read_height(bus, panel_id)
    if got != height:
        raise PanelError(f"panel {panel_id} reads {got} after the write, wanted {height}")


def parse_panel_ids(text):
    """Return the sorted ids of the panels on screen 0; sections nested below a containment are not panels."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    ids = set()
    for name in parser.sections():
        match = PANEL_SECTION.fullmatch(name)
        if match is None:
            continue
        section = parser[name]
        if section.get("plugin") == PANEL_PLUGIN and section.get("lastScreen") == "0":
            ids.add(int(match.group(1)))
    return sorted(ids)


def detect_panel(text):
    """Return (panel_id, None) when exactly one laptop panel is found, otherwise (None, reason)."""
    try:
        ids = parse_panel_ids(text)
    except configparser.Error as err:
        return None, f"cannot parse the appletsrc file ({err})"
    if not ids:
        return None, "no panel on screen 0 in the appletsrc file"
    if len(ids) > 1:
        return None, f"{len(ids)} panels on screen 0 in the appletsrc file ({', '.join(str(i) for i in ids)})"
    return ids[0], None


def read_panel(path):
    """Return (panel_id, None) for the single panel on screen 0 in the applet config at path, otherwise (None, reason)."""
    try:
        text = read_appletsrc(path)
    except OSError as err:
        return None, f"cannot read {path} ({err.strerror})"
    return detect_panel(text)


def docked_block(state, panel_id, panel_problem):
    """Return None when the saved panel is still the single panel on screen 0, so it may be restored while other screens are connected; otherwise the reason to wait."""
    if panel_problem is not None:
        return panel_problem
    if panel_id != state.panel_id:
        return f"the state file is for panel {state.panel_id} but the screen-0 panel is {panel_id}"
    return None


def load_state(path):
    """Return (State or None, problem or None); a missing file is (None, None)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, None
    except OSError as err:
        return None, f"cannot read the state file {path} ({err.strerror})"
    except ValueError:
        return None, f"the state file {path} is not valid JSON"
    panel_id = data.get("panel_id") if isinstance(data, dict) else None
    original = data.get("original_height") if isinstance(data, dict) else None
    if not is_int(panel_id) or panel_id < 0 or not is_int(original) or not MIN_HEIGHT <= original <= MAX_HEIGHT:
        return None, f"the state file {path} lacks a valid panel_id and original_height"
    return State(panel_id, original), None


def save_state(path, panel_id, original_height):
    """Create the state file; mode x makes a second save fail rather than overwrite the saved original."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "x", encoding="utf-8") as handle:
        json.dump({"panel_id": panel_id, "original_height": original_height}, handle)
        handle.write("\n")


class Service:
    """Each evaluation reads tablet mode and the screen count, then sets or restores the panel; the bus, scheduler and output are injected."""

    def __init__(self, config, bus, scheduler, out):
        self.config = config
        self.bus = bus
        self.scheduler = scheduler
        self.out = out
        self.notes = {}
        self.settle_pending = False

    def say(self, key, message):
        """Print a message unless it is the same as the last one under this key; clear() forgets the key."""
        if self.notes.get(key) != message:
            self.notes[key] = message
            self.out(message)

    def clear(self, key):
        self.notes.pop(key, None)

    def start(self):
        """Subscribe to the bus, check once now, then check every poll interval."""
        self.bus.subscribe(self.on_tablet_changed, self.on_name_appeared)
        self.evaluate()
        self.scheduler.repeat(self.config.poll_seconds, self.evaluate)

    def on_tablet_changed(self):
        self.evaluate()

    def on_name_appeared(self, _name):
        """KWin or plasmashell appeared; check after the settle delay, once however many names appear."""
        if not self.settle_pending:
            self.settle_pending = True
            self.scheduler.once(SETTLE_SECONDS, self.settle)

    def settle(self):
        self.settle_pending = False
        self.evaluate()

    def evaluate(self):
        """Decide from tablet mode and the screen count whether the panel should be tablet-sized, then set or restore it."""
        config = self.config
        screens = count_connected(config.drm_root)
        try:
            tablet = self.bus.read_tablet_mode()
        except BusError as err:
            self.say("kwin", f"cannot read tabletMode from {KWIN}: {err}")
            return
        self.clear("kwin")
        if not self.bus.name_has_owner(PLASMASHELL):
            self.say("plasmashell", f"{PLASMASHELL} is not on the bus; doing nothing until it appears")
            return
        self.clear("plasmashell")
        state, problem = load_state(config.state_file)
        if problem is not None:
            self.say("statefile", f"{problem}; the panel is left alone")
            return
        self.clear("statefile")
        tablet_on = tablet is True
        if tablet_on and screens == 1:
            self.bring_up(state)
        else:
            self.bring_down(state, tablet_on, screens)

    def bring_up(self, state):
        """Make the laptop panel tablet-sized, saving its original height first unless the state file already has one."""
        config = self.config
        panel_id, problem = read_panel(config.appletsrc)
        if problem is not None:
            self.say("panel", f"{problem}; the panel is left alone")
            return
        self.clear("panel")
        if state is not None and state.panel_id != panel_id:
            self.say("panel", f"the state file is for panel {state.panel_id} but the laptop panel is {panel_id}; both are left alone")
            return
        try:
            height = read_height(self.bus, panel_id)
        except (BusError, PanelError) as err:
            self.say("read", f"cannot read panel {panel_id} height: {err}")
            return
        self.clear("read")
        target = config.tablet_height
        if height == target:
            if state is None:
                self.say("decision", f"tablet mode on: panel {panel_id} is already at {target}; nothing saved")
            else:
                self.say("decision", f"tablet mode on: panel {panel_id} is at {target}; original height {state.original_height} is in the state file")
            return
        original = height if state is None else state.original_height
        if config.dry_run:
            if state is None:
                self.say("decision", f"would save height {height} as the original, then set panel {panel_id} to {target}")
            else:
                self.say("decision", f"would set panel {panel_id} to {target}; original height {original} is in the state file")
            return
        if state is None:
            try:
                save_state(config.state_file, panel_id, height)
            except OSError as err:
                self.say("save", f"cannot write the state file, the panel is left alone: {err}")
                return
            self.clear("save")
        try:
            write_and_verify(self.bus, panel_id, target)
        except (BusError, PanelError) as err:
            self.say("write", f"cannot set panel {panel_id} to {target}, will retry: {err}")
            return
        self.clear("write")
        self.say("decision", f"tablet mode on: panel {panel_id} set to {target}; original height {original} is in the state file")

    def bring_down(self, state, tablet_on, screens):
        """Put the panel back to its original height: when the laptop is the only screen, or while other screens are connected and the saved panel is still the single panel on screen 0."""
        if state is None:
            if tablet_on:
                self.say("decision", f"tablet mode on but {screens} screens are connected: the panel is left alone")
            else:
                self.say("decision", "tablet mode off: nothing to restore")
            return
        if screens == 0:
            self.say("decision", f"0 screens connected: panel {state.panel_id} keeps its tablet height and the state file is kept until a screen is connected")
            return
        if screens == 1:
            self.restore(state, "tablet mode off")
            return
        block = self.check_docked(state)
        if block is not None:
            self.say("dock", f"{screens} screens connected: {block}; the panel is left alone and the state file is kept")
            return
        self.clear("dock")
        mode = "tablet mode on" if tablet_on else "tablet mode off"
        self.restore(state, f"{mode} with {screens} screens connected")

    def check_docked(self, state):
        """Return None when the saved panel may be restored while other screens are connected, otherwise why not; it reads the appletsrc file and never the bus."""
        panel_id, problem = read_panel(self.config.appletsrc)
        return docked_block(state, panel_id, problem)

    def restore(self, state, reason):
        """Write the saved height back and delete the state file; a failure keeps the state file for the next check."""
        panel_id, original = state.panel_id, state.original_height
        if self.config.dry_run:
            self.say("decision", f"would restore panel {panel_id} to {original} and delete the state file")
            return
        try:
            write_and_verify(self.bus, panel_id, original)
        except (BusError, PanelError) as err:
            self.say("restore", f"cannot restore panel {panel_id} to {original}, keeping the state file: {err}")
            return
        self.clear("restore")
        try:
            os.remove(self.config.state_file)
        except OSError as err:
            self.say("delete", f"restored panel {panel_id} but cannot delete the state file: {err}")
            return
        self.clear("delete")
        self.say("decision", f"{reason}: restored panel {panel_id} to {original}")

    def stop(self):
        """On SIGTERM or SIGINT, put the saved height back when the laptop is the only screen, or while other screens are connected and the saved panel is still the single panel on screen 0; otherwise keep the state file and say why."""
        state, problem = load_state(self.config.state_file)
        if problem is not None:
            self.out(f"{problem}; the panel is left alone")
            return
        if state is None:
            return
        screens = count_connected(self.config.drm_root)
        if screens == 0:
            self.out(f"stopping with 0 screens connected: panel {state.panel_id} keeps its tablet height and the state file is kept")
            return
        if screens != 1:
            block = self.check_docked(state)
            if block is not None:
                self.out(f"stopping with {screens} screens connected: {block}; the panel is left alone and the state file is kept")
                return
        self.restore(state, "service stopping")


def describe_next(tablet, plasma_up, screens, panel_id, panel_problem, state, state_problem, height, target):
    """Return the one-line answer to what the service would do now, for --status."""
    if tablet is None:
        return "nothing until KWin answers"
    if state_problem is not None:
        return "nothing, the state file needs attention"
    if tablet is True and screens == 1:
        if not plasma_up:
            return f"nothing until {PLASMASHELL} is on the bus"
        if panel_id is None:
            return f"nothing, {panel_problem}"
        if state is not None and state.panel_id != panel_id:
            return "nothing, the state file is for another panel"
        if height is None:
            return "nothing until the panel height can be read"
        if height == target:
            return f"nothing, panel {panel_id} is already at {target}"
        if state is None:
            return f"save height {height} as the original, then set panel {panel_id} to {target}"
        return f"set panel {panel_id} to {target}, keeping the saved original height {state.original_height}"
    if state is None:
        return "nothing to restore" if tablet is False else f"nothing, {screens} screens are connected"
    if screens == 0:
        return f"nothing, {screens} screens are connected, so the saved state is kept"
    if screens != 1:
        block = docked_block(state, panel_id, panel_problem)
        if block is not None:
            return f"nothing, {block}, so the saved state is kept"
        if not plasma_up:
            return f"nothing until {PLASMASHELL} is on the bus, then restore panel {state.panel_id} to {state.original_height} (the saved screen-0 panel)"
        return f"restore panel {state.panel_id} to {state.original_height} and delete the state file (the saved screen-0 panel)"
    if not plasma_up:
        return f"nothing until {PLASMASHELL} is on the bus, then restore panel {state.panel_id} to {state.original_height}"
    return f"restore panel {state.panel_id} to {state.original_height} and delete the state file"


def run_status(config, bus, out):
    """Print one check of tablet mode, screens, the panel and the state file, and what the service would do now; it never writes."""
    screens = count_connected(config.drm_root)
    tablet = None
    try:
        tablet = bus.read_tablet_mode()
    except BusError as err:
        out(f"tablet mode: unknown ({err})")
    else:
        out(f"tablet mode: {'on' if tablet is True else 'off'}")
    out(f"connected screens: {screens}")
    panel_id, panel_problem = read_panel(config.appletsrc)
    out(f"panel id: {panel_id}" if panel_id is not None else f"panel id: none ({panel_problem})")
    state, state_problem = load_state(config.state_file)
    if state_problem is not None:
        out(f"state file: unreadable ({state_problem})")
    elif state is None:
        out("state file: absent")
    else:
        out(f"state file: present (panel {state.panel_id}, original height {state.original_height})")
    plasma_up = bus.name_has_owner(PLASMASHELL)
    height = None
    if screens != 1:
        out(f"panel height: not queried, {screens} screens are connected")
    elif not plasma_up:
        out(f"panel height: not queried, {PLASMASHELL} is not on the bus")
    elif panel_id is None:
        out("panel height: not queried, no single panel id")
    else:
        try:
            height = read_height(bus, panel_id)
        except (BusError, PanelError) as err:
            out(f"panel height: unreadable ({err})")
        else:
            out(f"panel height: {height}")
    out("would do: " + describe_next(tablet, plasma_up, screens, panel_id, panel_problem, state,
                                     state_problem, height, config.tablet_height))
    return 0


def gi_modules():
    """Return (Gio, GLib); BusError when PyGObject is not installed, so the status check can still report that."""
    try:
        from gi.repository import Gio, GLib
    except ImportError as err:
        raise BusError(f"PyGObject is not available ({err})") from None
    return Gio, GLib


class PlasmaBus:
    """The session-bus client; one connection is kept for the life of the process."""

    def __init__(self):
        self._conn = None

    def _connection(self):
        Gio, GLib = gi_modules()
        if self._conn is None:
            try:
                self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error as err:
                raise BusError(err.message) from None
        return self._conn

    def _call(self, bus_name, path, iface, method, signature, args, reply_type):
        Gio, GLib = gi_modules()
        connection = self._connection()
        try:
            reply = connection.call_sync(bus_name, path, iface, method, GLib.Variant(signature, args),
                                         GLib.VariantType.new(reply_type), Gio.DBusCallFlags.NONE,
                                         CALL_TIMEOUT_MS, None)
        except GLib.Error as err:
            raise BusError(err.message) from None
        return reply.unpack()

    def name_has_owner(self, name):
        try:
            (present,) = self._call(BUS_DAEMON, BUS_PATH, BUS_DAEMON, "NameHasOwner", "(s)", (name,), "(b)")
        except BusError:
            return False
        return bool(present)

    def read_tablet_mode(self):
        (value,) = self._call(KWIN, KWIN_PATH, PROPS, "Get", "(ss)", (TABLET_IFACE, "tabletMode"), "(v)")
        return value

    def evaluate(self, script):
        (text,) = self._call(PLASMASHELL, PLASMASHELL_PATH, PLASMASHELL_IFACE, "evaluateScript",
                             "(s)", (script,), "(s)")
        return text

    def subscribe(self, on_tablet_changed, on_name_appeared):
        """Watch tabletModeChanged and the appearance of KWin and plasmashell; BusError when there is no session bus."""
        Gio, GLib = gi_modules()
        connection = self._connection()
        try:
            connection.signal_subscribe(KWIN, TABLET_IFACE, "tabletModeChanged", KWIN_PATH, None,
                                        Gio.DBusSignalFlags.NONE, lambda *_args: on_tablet_changed())
            for name in (KWIN, PLASMASHELL):
                Gio.bus_watch_name(Gio.BusType.SESSION, name, Gio.BusNameWatcherFlags.NONE,
                                   lambda *_args, name=name: on_name_appeared(name),
                                   lambda *_args: None)
        except GLib.Error as err:
            raise BusError(err.message) from None


class GLibScheduler:
    """Runs the poll and the settle timer on a GLib main loop; SIGTERM and SIGINT end the loop, and main then restores the panel."""

    def __init__(self):
        from gi.repository import GLib, GLibUnix
        self._glib = GLib
        self._unix = GLibUnix
        self._loop = GLib.MainLoop()

    def repeat(self, seconds, callback):
        self._glib.timeout_add_seconds(seconds, self._tick, callback)

    def once(self, seconds, callback):
        self._glib.timeout_add_seconds(seconds, self._fire_once, callback)

    @staticmethod
    def _tick(callback):
        callback()
        return True

    @staticmethod
    def _fire_once(callback):
        callback()
        return False

    def _quit(self):
        self._loop.quit()
        return False

    def run(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._unix.signal_add(self._glib.PRIORITY_DEFAULT, signum, self._quit)
        self._loop.run()


def height_arg(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if not MIN_HEIGHT <= value <= MAX_HEIGHT:
        raise argparse.ArgumentTypeError(f"must be from {MIN_HEIGHT} to {MAX_HEIGHT}: {text}")
    return value


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
        description="Make the laptop's Plasma panel taller for touch while KWin is in tablet mode and the laptop is the only screen.")
    parser.add_argument("--tablet-height", type=height_arg, default=DEFAULT_TABLET_HEIGHT, metavar="N",
                        help=f"panel height in tablet mode, {MIN_HEIGHT} to {MAX_HEIGHT} (default %(default)s)")
    parser.add_argument("--appletsrc", default=DEFAULT_APPLETSRC, metavar="PATH",
                        help="Plasma applet config to read (default %(default)s)")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, metavar="PATH",
                        help="where the saved original height is kept (default %(default)s)")
    parser.add_argument("--drm-root", default=DEFAULT_DRM_ROOT, metavar="PATH",
                        help="where screen connection status is read (default %(default)s)")
    parser.add_argument("--poll-seconds", type=positive_int, default=DEFAULT_POLL_SECONDS, metavar="N",
                        help="seconds between checks (default %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="log what would change; never write the panel height or the state file")
    parser.add_argument("--status", action="store_true",
                        help="print one check of tablet mode, screens, the panel and the state file, then exit; never writes")
    return parser


def default_deps():
    return Deps(PlasmaBus, GLibScheduler, emit)


def main(argv=None, deps=None):
    deps = deps or default_deps()
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config(
        tablet_height=args.tablet_height,
        appletsrc=os.path.expanduser(args.appletsrc),
        state_file=os.path.expanduser(args.state_file),
        drm_root=args.drm_root,
        poll_seconds=args.poll_seconds,
        dry_run=args.dry_run)
    bus = deps.bus_factory()
    if args.status:
        return run_status(config, bus, deps.out)
    try:
        scheduler = deps.scheduler_factory()
    except ImportError as err:
        deps.out(f"PyGObject is not available: {err}")
        return 1
    service = Service(config, bus, scheduler, deps.out)
    try:
        service.start()
        scheduler.run()
    except BusError as err:
        deps.out(f"cannot reach the session bus: {err}")
        return 1
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
