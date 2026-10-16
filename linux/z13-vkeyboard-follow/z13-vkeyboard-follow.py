#!/usr/bin/python3
"""Set KWin's virtual keyboard mode from tablet mode, so Plasma Keyboard stays hidden while the keyboard folio is attached."""

import os
import signal
import sys

from gi.repository import Gio, GLib, GLibUnix

KWIN = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin"
TABLET_IFACE = "org.kde.KWin.TabletModeManager"
VK_PATH = "/VirtualKeyboard"
VK_IFACE = "org.kde.kwin.VirtualKeyboard"
PROPS = "org.freedesktop.DBus.Properties"
ATTACHED_ENV = "Z13_VKEYBOARD_ATTACHED_MODE"
DETACHED_ENV = "Z13_VKEYBOARD_DETACHED_MODE"
DEFAULT_ATTACHED_MODE = 0  # KWin: never show
DEFAULT_DETACHED_MODE = 1  # KWin: show for touch and pen only
APPEAR_SETTLE_MS = 1500  # apply again this long after KWin appears, in case its virtual keyboard object is not exported yet
RETRY_DELAY_MS = 2000
RETRY_LIMIT = 3  # retries in a row after a failed refresh


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


class Follower:
    def __init__(self, bus, keyboard, attached_mode, detached_mode, schedule=schedule_once):
        self.bus = bus
        self.keyboard = keyboard
        self.attached_mode = attached_mode
        self.detached_mode = detached_mode
        self.schedule = schedule
        self.kwin_up = False
        self.retrier = Retrier(self.refresh, schedule)

    def refresh(self):
        """Read tablet mode from KWin and apply the matching virtual keyboard mode. Returns False if a call failed."""
        try:
            tablet_on = get_prop(self.bus, KWIN, KWIN_PATH, TABLET_IFACE, "tabletMode")
        except GLib.Error as err:
            log(f"z13-vkeyboard-follow: cannot read tablet mode: {err.message}")
            return False
        return apply_mode(self.keyboard, tablet_on, self.attached_mode, self.detached_mode)

    def subscribe(self):
        self.bus.signal_subscribe(KWIN, TABLET_IFACE, "tabletModeChanged", KWIN_PATH, None,
                                  Gio.DBusSignalFlags.NONE, self.on_tablet_signal)
        self.bus.signal_subscribe(KWIN, PROPS, "PropertiesChanged", KWIN_PATH, TABLET_IFACE,
                                  Gio.DBusSignalFlags.NONE, self.on_properties_changed)

    def watch_kwin(self):
        Gio.bus_watch_name(Gio.BusType.SESSION, KWIN, Gio.BusNameWatcherFlags.NONE,
                           self.on_kwin_appeared, self.on_kwin_vanished)

    def on_kwin_appeared(self, *_args):
        """Apply now, and again after APPEAR_SETTLE_MS, because KWin's virtual keyboard object may not be exported yet."""
        self.kwin_up = True
        self.retrier.run()
        self.schedule(APPEAR_SETTLE_MS, self.on_settle)

    def on_settle(self):
        if self.kwin_up:
            self.retrier.run()

    def on_kwin_vanished(self, *_args):
        """Drop any pending retry and wait for KWin to come back."""
        self.kwin_up = False
        self.retrier.cancel()

    def on_tablet_signal(self, *_args):
        self.retrier.run()

    def on_properties_changed(self, _conn, _sender, _path, _iface, _signal, params, *_):
        _changed_iface, changed, _invalidated = params.unpack()
        if "tabletMode" in changed:
            self.retrier.run()


def quit_loop(loop):
    loop.quit()
    return GLib.SOURCE_REMOVE


def main():
    try:
        attached_mode, detached_mode = load_modes(os.environ)
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
    follower = Follower(bus, KWinVirtualKeyboard(bus), attached_mode, detached_mode)
    follower.subscribe()
    follower.watch_kwin()
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
