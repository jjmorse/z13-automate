#!/usr/bin/python3
"""Follow KWin tablet mode: Onboard auto-show runs only while the keyboard folio is detached."""

import argparse
import signal
import sys

from gi.repository import Gio, GLib

KWIN = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin"
TABLET_IFACE = "org.kde.KWin.TabletModeManager"
ONBOARD = "org.onboard.Onboard"
ONBOARD_PATH = "/org/onboard/Onboard/Keyboard"
ONBOARD_IFACE = "org.onboard.Onboard.Keyboard"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
PROPS = "org.freedesktop.DBus.Properties"
NAMES = {KWIN: "KWin", ONBOARD: "Onboard"}


def call(bus, dest, path, iface, method, params=None):
    return bus.call_sync(dest, path, iface, method, params, None, Gio.DBusCallFlags.NONE, -1, None)


def get_prop(bus, dest, path, iface, name):
    return call(bus, dest, path, PROPS, "Get", GLib.Variant("(ss)", (iface, name))).unpack()[0]


def has_owner(bus, name):
    return call(bus, DBUS, DBUS_PATH, DBUS, "NameHasOwner", GLib.Variant("(s)", (name,))).unpack()[0]


def set_paused(bus, paused):
    call(bus, ONBOARD, ONBOARD_PATH, PROPS, "Set",
         GLib.Variant("(ssv)", (ONBOARD_IFACE, "AutoShowPaused", GLib.Variant("b", paused))))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Watcher:
    def __init__(self, bus):
        self.bus = bus
        self.waiting = set()

    def ready(self, name):
        """Return True if the name has an owner; say 'waiting for ...' once per absence."""
        if has_owner(self.bus, name):
            self.waiting.discard(name)
            return True
        if name not in self.waiting:
            self.waiting.add(name)
            print(f"waiting for {NAMES[name]}", flush=True)
        return False

    def refresh(self):
        """Read KWin's tablet-mode flag and apply it to Onboard."""
        try:
            if not self.ready(KWIN):
                return
            tablet_on = get_prop(self.bus, KWIN, KWIN_PATH, TABLET_IFACE, "tabletMode")
            if not self.ready(ONBOARD):
                return
            if tablet_on:
                set_paused(self.bus, False)
                print("tablet mode on: auto-show active", flush=True)
            else:
                set_paused(self.bus, True)
                call(self.bus, ONBOARD, ONBOARD_PATH, ONBOARD_IFACE, "Hide")
                print("tablet mode off: auto-show paused and keyboard hidden", flush=True)
        except GLib.Error as err:
            log(f"z13-onboard-follow: {err.message}")

    def subscribe(self):
        self.bus.signal_subscribe(KWIN, TABLET_IFACE, "tabletModeChanged", KWIN_PATH, None,
                                  Gio.DBusSignalFlags.NONE, self.on_event)
        self.bus.signal_subscribe(KWIN, PROPS, "PropertiesChanged", KWIN_PATH, TABLET_IFACE,
                                  Gio.DBusSignalFlags.NONE, self.on_event)
        for name in (ONBOARD, KWIN):
            self.bus.signal_subscribe(DBUS, DBUS, "NameOwnerChanged", DBUS_PATH, name,
                                      Gio.DBusSignalFlags.NONE, self.on_owner_changed)

    def on_event(self, *_args):
        self.refresh()

    def on_owner_changed(self, _conn, _sender, _path, _iface, _signal, params, *_):
        _name, _old, new_owner = params.unpack()
        if new_owner:
            # Onboard exports its D-Bus object a moment after it takes the name
            GLib.timeout_add(1500, self.refresh_later)

    def refresh_later(self):
        self.refresh()
        return GLib.SOURCE_REMOVE


def show_status(bus):
    try:
        tablet_on = get_prop(bus, KWIN, KWIN_PATH, TABLET_IFACE, "tabletMode")
        print(f"tablet mode: {'on' if tablet_on else 'off'}", flush=True)
    except GLib.Error as err:
        print(f"tablet mode: unknown ({err.message})", flush=True)
    try:
        if has_owner(bus, ONBOARD):
            paused = get_prop(bus, ONBOARD, ONBOARD_PATH, ONBOARD_IFACE, "AutoShowPaused")
            print(f"AutoShowPaused: {'true' if paused else 'false'}", flush=True)
        else:
            print("AutoShowPaused: not running", flush=True)
    except GLib.Error as err:
        print(f"AutoShowPaused: unknown ({err.message})", flush=True)
    return 0


def quit_loop(loop):
    loop.quit()
    return GLib.SOURCE_REMOVE


def main():
    parser = argparse.ArgumentParser(description="Pause Onboard auto-show while the keyboard folio is attached.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="apply the current state once and exit")
    mode.add_argument("--status", action="store_true", help="print tablet mode and AutoShowPaused; change nothing")
    args = parser.parse_args()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as err:
        log(f"z13-onboard-follow: no session bus: {err.message}")
        return 1
    if args.status:
        return show_status(bus)
    watcher = Watcher(bus)
    if args.once:
        watcher.refresh()
        return 0
    loop = GLib.MainLoop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, quit_loop, loop)
    watcher.subscribe()
    watcher.refresh()
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
