#!/usr/bin/python3
"""Set KWin's virtual keyboard mode from tablet mode, so Plasma Keyboard stays hidden while the keyboard folio is attached, and restart a stale keyboard process when tablet mode turns on or the system resumes in tablet mode."""

import os
import signal
import sys
import time

from gi.repository import Gio, GLib, GLibUnix

KWIN = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin"
TABLET_IFACE = "org.kde.KWin.TabletModeManager"
VK_PATH = "/VirtualKeyboard"
VK_IFACE = "org.kde.kwin.VirtualKeyboard"
PROPS = "org.freedesktop.DBus.Properties"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
DBUS_IFACE = "org.freedesktop.DBus"
LOGIND = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
LOGIND_IFACE = "org.freedesktop.login1.Manager"
ATTACHED_ENV = "Z13_VKEYBOARD_ATTACHED_MODE"
DETACHED_ENV = "Z13_VKEYBOARD_DETACHED_MODE"
RESTART_ENV = "Z13_VKEYBOARD_RESTART"
PROCESS_ENV = "Z13_VKEYBOARD_PROCESS"
DEFAULT_ATTACHED_MODE = 0  # KWin: never show
DEFAULT_DETACHED_MODE = 1  # KWin: show for touch and pen only
DEFAULT_PROCESS = "plasma-keyboard"
APPEAR_SETTLE_MS = 1500  # apply again this long after KWin appears, in case its virtual keyboard object is not exported yet
RETRY_DELAY_MS = 2000
RETRY_LIMIT = 3  # retries in a row after a failed refresh
RESTART_MIN_INTERVAL_S = 30  # KWin gives up on an input method after 5 crashes in 20 s
VERIFY_DELAY_MS = 3000
RESUME_DELAY_MS = 2000


def log(msg):
    print(msg, flush=True)


def desired_mode(tablet_on, attached_mode, detached_mode):
    """Return the KWin virtual keyboard mode for the tablet-mode flag."""
    return detached_mode if tablet_on else attached_mode


def mode_from_env(env, name, default):
    text = env.get(name)
    if text is None:
        return default
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{name} must be an integer, not {text!r}") from None


def load_modes(env):
    """Return (attached, detached) modes from an environment mapping; raise ValueError on a non-integer."""
    return mode_from_env(env, ATTACHED_ENV, DEFAULT_ATTACHED_MODE), mode_from_env(env, DETACHED_ENV, DEFAULT_DETACHED_MODE)


def load_restart_settings(env):
    """Return (enabled, process name) from an environment mapping; raise ValueError on a bad value."""
    text = env.get(RESTART_ENV, "1")
    if text not in ("0", "1"):
        raise ValueError(f"{RESTART_ENV} must be 0 or 1, not {text!r}")
    name = env.get(PROCESS_ENV, DEFAULT_PROCESS)
    if not name:
        raise ValueError(f"{PROCESS_ENV} must not be empty")
    return text == "1", name


def apply_mode(keyboard, tablet_on, attached_mode, detached_mode):
    """Set the keyboard mode for the tablet-mode flag, writing only when it differs. A failed call is logged and returns False."""
    want = desired_mode(tablet_on, attached_mode, detached_mode)
    label = "tablet mode on" if tablet_on else "tablet mode off"
    try:
        current = keyboard.get_mode()
    except GLib.Error as err:
        log(f"z13-vkeyboard-follow: cannot read virtual keyboard mode: {err.message}")
        return False
    if current == want:
        log(f"{label}: virtual keyboard mode already {want}")
        return True
    try:
        keyboard.set_mode(want)
    except GLib.Error as err:
        log(f"z13-vkeyboard-follow: cannot set virtual keyboard mode to {want}: {err.message}")
        return False
    log(f"{label}: virtual keyboard mode set to {want}")
    return True


def schedule_once(delay_ms, callback):
    """Run callback once, delay_ms from now, on the GLib main loop."""

    def fire():
        callback()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(delay_ms, fire)


class Retrier:
    """Runs attempt() now, and again delay_ms later while it returns False, up to limit retries in a row.

    Each run() from an event starts a new streak, and cancel() drops any retry still waiting.
    schedule(delay_ms, callback) runs callback once later; the tests pass a fake in its place.
    """

    def __init__(self, attempt, schedule, delay_ms=RETRY_DELAY_MS, limit=RETRY_LIMIT):
        self.attempt = attempt
        self.schedule = schedule
        self.delay_ms = delay_ms
        self.limit = limit
        self.failures = 0
        self.generation = 0

    def run(self):
        self.generation += 1
        self.failures = 0
        self._attempt()

    def cancel(self):
        self.generation += 1
        self.failures = 0

    def _attempt(self):
        if self.attempt():
            self.failures = 0
            return
        if self.failures < self.limit:
            self.failures += 1
            generation = self.generation
            self.schedule(self.delay_ms, lambda: self._retry(generation))

    def _retry(self, generation):
        if generation == self.generation:
            self._attempt()


def call(bus, dest, path, iface, method, params=None):
    return bus.call_sync(dest, path, iface, method, params, None, Gio.DBusCallFlags.NONE, -1, None)


def get_prop(bus, dest, path, iface, name):
    return call(bus, dest, path, PROPS, "Get", GLib.Variant("(ss)", (iface, name))).unpack()[0]


class KWinVirtualKeyboard:
    """The read/write mode property of KWin's virtual keyboard object."""

    def __init__(self, bus):
        self.bus = bus

    def get_mode(self):
        return get_prop(self.bus, KWIN, VK_PATH, VK_IFACE, "mode")

    def set_mode(self, mode):
        call(self.bus, KWIN, VK_PATH, PROPS, "Set",
             GLib.Variant("(ssv)", (VK_IFACE, "mode", GLib.Variant("i", mode))))


def read_proc(proc_root, entry, leaf):
    with open(os.path.join(proc_root, entry, leaf)) as f:
        return f.read()


def real_uid(status_text):
    """Return the real uid from the Uid: line of a /proc status file, or None if there is no such line."""
    for line in status_text.splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    return None


def is_keyboard_process(proc_root, entry, name, kwin_pid, uid):
    """Return True if the /proc entry is a process named name, started by kwin_pid, with real uid uid."""
    if read_proc(proc_root, entry, "comm").strip() != name:
        return False
    # The comm field in stat sits in parentheses and may hold spaces or ')', so the fields start after the last ')'.
    stat = read_proc(proc_root, entry, "stat")
    ppid = int(stat[stat.rindex(")") + 1:].split()[1])
    return ppid == kwin_pid and real_uid(read_proc(proc_root, entry, "status")) == uid


def find_keyboard_pids(kwin_pid, name, uid, proc_root="/proc"):
    """Return the sorted pids of processes named name under kwin_pid, for real uid uid. Entries that cannot be read are skipped."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return []
    pids = []
    for entry in entries:
        if not (entry.isascii() and entry.isdigit()):
            continue
        try:
            if is_keyboard_process(proc_root, entry, name, kwin_pid, uid):
                pids.append(int(entry))
        except (OSError, ValueError, IndexError):
            pass  # the process exited, or its files are unreadable or malformed
    return sorted(pids)


class KWinProcess:
    """KWin's pid as the session bus daemon reports it."""

    def __init__(self, bus):
        self.bus = bus

    def pid(self):
        """Return KWin's pid, or None when the bus daemon does not know it."""
        try:
            reply = call(self.bus, DBUS, DBUS_PATH, DBUS_IFACE, "GetConnectionUnixProcessID", GLib.Variant("(s)", (KWIN,)))
        except GLib.Error:
            return None
        return reply.unpack()[0]


class Restarter:
    """Kills the on-screen keyboard process so KWin relaunches it, then checks that it came back.

    At most one restart runs per min_interval_s. The collaborators are injected so the tests need no real system: kwin_pid() returns KWin's pid or None, find(kwin_pid, name, uid) lists the keyboard pids, kill(pid, sig) sends a signal, clock() returns seconds, and schedule(delay_ms, callback) runs callback later.
    """

    def __init__(self, kwin_pid, name, uid, find=find_keyboard_pids, kill=os.kill, clock=time.monotonic,
                 schedule=schedule_once, min_interval_s=RESTART_MIN_INTERVAL_S, verify_delay_ms=VERIFY_DELAY_MS):
        self.kwin_pid = kwin_pid
        self.name = name
        self.uid = uid
        self.find = find
        self.kill = kill
        self.clock = clock
        self.schedule = schedule
        self.min_interval_s = min_interval_s
        self.verify_delay_ms = verify_delay_ms
        self.last_restart = None

    def restart(self, reason):
        now = self.clock()
        if self.last_restart is not None and now - self.last_restart < self.min_interval_s:
            log(f"z13-vkeyboard-follow: restart skipped ({reason}): last restart was {int(now - self.last_restart)} s ago")
            return
        kwin = self.kwin_pid()
        if kwin is None:
            log(f"z13-vkeyboard-follow: restart skipped ({reason}): KWin pid unknown")
            return
        pids = self.find(kwin, self.name, self.uid)
        if not pids:
            log(f"no {self.name} process under KWin (pid {kwin}); nothing to restart")
            return
        killed = []
        for pid in pids:
            try:
                self.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                log(f"z13-vkeyboard-follow: pid {pid} was already gone")
            except PermissionError as err:
                log(f"z13-vkeyboard-follow: cannot kill pid {pid}: {err.strerror}")
            else:
                killed.append(pid)
        self.last_restart = now
        if not killed:
            return
        log(f"restarted the on-screen keyboard ({reason}): killed pid {', '.join(map(str, killed))}")
        self.schedule(self.verify_delay_ms, lambda: self.verify(kwin, killed))

    def verify(self, kwin, killed):
        new = [pid for pid in self.find(kwin, self.name, self.uid) if pid not in killed]
        if new:
            log(f"on-screen keyboard is back as pid {', '.join(map(str, new))}")
        else:
            log(f"z13-vkeyboard-follow: on-screen keyboard did not come back within {self.verify_delay_ms / 1000:g} s "
                f"(killed pid {', '.join(map(str, killed))}); KWin may have given up after repeated crashes")


class Follower:
    def __init__(self, bus, keyboard, attached_mode, detached_mode, schedule=schedule_once, restarter=None):
        self.bus = bus
        self.keyboard = keyboard
        self.attached_mode = attached_mode
        self.detached_mode = detached_mode
        self.schedule = schedule
        self.restarter = restarter
        self.kwin_up = False
        self.last_tablet = None  # tablet-mode value of the last refresh that succeeded; None until there is one
        self.retrier = Retrier(self.refresh, schedule)

    def refresh(self):
        """Read tablet mode from KWin and apply the matching virtual keyboard mode. Returns False if a call failed. Restarts the keyboard when tablet mode has just turned on."""
        try:
            tablet_on = get_prop(self.bus, KWIN, KWIN_PATH, TABLET_IFACE, "tabletMode")
        except GLib.Error as err:
            log(f"z13-vkeyboard-follow: cannot read tablet mode: {err.message}")
            return False
        if not apply_mode(self.keyboard, tablet_on, self.attached_mode, self.detached_mode):
            return False
        was_on = self.last_tablet
        self.last_tablet = tablet_on
        if was_on is False and tablet_on and self.restarter is not None:
            self.restarter.restart("tablet mode on")
        return True

    def subscribe(self):
        self.bus.signal_subscribe(KWIN, TABLET_IFACE, "tabletModeChanged", KWIN_PATH, None,
                                  Gio.DBusSignalFlags.NONE, self.on_tablet_signal)
        self.bus.signal_subscribe(KWIN, PROPS, "PropertiesChanged", KWIN_PATH, TABLET_IFACE,
                                  Gio.DBusSignalFlags.NONE, self.on_properties_changed)

    def watch_kwin(self):
        Gio.bus_watch_name(Gio.BusType.SESSION, KWIN, Gio.BusNameWatcherFlags.NONE,
                           self.on_kwin_appeared, self.on_kwin_vanished)

    def subscribe_sleep(self, system_bus):
        system_bus.signal_subscribe(LOGIND, LOGIND_IFACE, "PrepareForSleep", LOGIND_PATH, None,
                                    Gio.DBusSignalFlags.NONE, self.on_prepare_for_sleep)

    def on_kwin_appeared(self, *_args):
        """Apply now, and again after APPEAR_SETTLE_MS, because KWin's virtual keyboard object may not be exported yet."""
        self.kwin_up = True
        self.retrier.run()
        self.schedule(APPEAR_SETTLE_MS, self.on_settle)

    def on_settle(self):
        if self.kwin_up:
            self.retrier.run()

    def on_kwin_vanished(self, *_args):
        """Drop any pending retry, forget the last tablet-mode value, and wait for KWin to come back."""
        self.kwin_up = False
        self.last_tablet = None  # KWin's return is a first reading, so it does not restart the keyboard
        self.retrier.cancel()

    def on_tablet_signal(self, *_args):
        self.retrier.run()

    def on_properties_changed(self, _conn, _sender, _path, _iface, _signal, params, *_):
        _changed_iface, changed, _invalidated = params.unpack()
        if "tabletMode" in changed:
            self.retrier.run()

    def on_prepare_for_sleep(self, _conn, _sender, _path, _iface, _signal, params, *_):
        """On resume, restart the keyboard RESUME_DELAY_MS later if tablet mode was on before the sleep."""
        (going_to_sleep,) = params.unpack()
        if going_to_sleep or not self.last_tablet or self.restarter is None:
            return
        self.schedule(RESUME_DELAY_MS, self.on_resume_settled)

    def on_resume_settled(self):
        """Restart after the resume, unless tablet mode has ended while the devices settled."""
        if self.last_tablet:
            self.restarter.restart("resume from sleep")


def quit_loop(loop):
    loop.quit()
    return GLib.SOURCE_REMOVE


def main():
    try:
        attached_mode, detached_mode = load_modes(os.environ)
        restart_enabled, process_name = load_restart_settings(os.environ)
    except ValueError as err:
        log(f"z13-vkeyboard-follow: {err}")
        return 2
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as err:
        log(f"z13-vkeyboard-follow: no session bus: {err.message}")
        return 1
    loop = GLib.MainLoop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, quit_loop, loop)
    restarter = Restarter(KWinProcess(bus).pid, process_name, os.getuid()) if restart_enabled else None
    follower = Follower(bus, KWinVirtualKeyboard(bus), attached_mode, detached_mode, restarter=restarter)
    follower.subscribe()
    follower.watch_kwin()
    if restart_enabled:
        try:
            system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as err:
            log(f"z13-vkeyboard-follow: no system bus, so no restart on resume: {err.message}")
        else:
            follower.subscribe_sleep(system_bus)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
