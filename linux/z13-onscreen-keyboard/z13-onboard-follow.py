#!/usr/bin/python3
"""Follow KWin tablet mode and external keyboards: Onboard auto-show runs only in tablet mode with no other keyboard."""

import argparse
import signal
import subprocess
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
IGNORED_VENDORS = {"0b05"}  # ASUS: the folio and internal keys, which tablet mode already covers
UDEV_MONITOR = ["udevadm", "monitor", "--udev", "--subsystem-match=input"]


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


def external_keyboards(db_text):
    """Return sorted unique names of external USB or Bluetooth keyboards in `udevadm info --export-db` text."""
    names = set()
    for block in db_text.split("\n\n"):
        props = dict(line[3:].split("=", 1) for line in block.splitlines() if line.startswith("E: ") and "=" in line)
        vendor = props.get("ID_VENDOR_ID", "")
        if (props.get("ID_INPUT_KEYBOARD") == "1"
                and props.get("DEVNAME", "").startswith("/dev/input/event")
                and not props.get("DEVPATH", "").startswith("/devices/virtual/")
                and props.get("ID_BUS") in ("usb", "bluetooth")
                and vendor not in IGNORED_VENDORS):
            model = props.get("ID_MODEL")
            names.add(model.replace("_", " ") if model else f"{vendor}:{props.get('ID_MODEL_ID', '')}")
    return sorted(names)


def read_external_keyboards():
    try:
        done = subprocess.run(["udevadm", "info", "--export-db"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as err:
        log(f"z13-onboard-follow: cannot run udevadm: {err}")
        return []
    if done.returncode != 0:
        log(f"z13-onboard-follow: udevadm info failed with exit status {done.returncode}")
        return []
    return external_keyboards(done.stdout)


class Watcher:
    def __init__(self, bus):
        self.bus = bus
        self.waiting = set()
        self.loop = None
        self.exit_code = 0
        self.refresh_scheduled = False

    def ready(self, name):
        """Return True if the name has an owner; say 'waiting for ...' once per absence."""
        if has_owner(self.bus, name):
            self.waiting.discard(name)
            return True
        if name not in self.waiting:
            self.waiting.add(name)
            print(f"waiting for {NAMES[name]}", flush=True)
        return False

    def set_auto_show(self, active, msg):
        set_paused(self.bus, not active)
        if not active:
            call(self.bus, ONBOARD, ONBOARD_PATH, ONBOARD_IFACE, "Hide")
        print(msg, flush=True)

    def refresh(self):
        """Apply tablet mode and external keyboards to Onboard."""
        try:
            if not self.ready(KWIN):
                return
            tablet_on = get_prop(self.bus, KWIN, KWIN_PATH, TABLET_IFACE, "tabletMode")
            if not self.ready(ONBOARD):
                return
            keyboards = read_external_keyboards()
            if not tablet_on:
                self.set_auto_show(False, "tablet mode off: auto-show paused and keyboard hidden")
            elif keyboards:
                self.set_auto_show(False, f"tablet mode on, external keyboard ({', '.join(keyboards)}): auto-show paused and keyboard hidden")
            else:
                self.set_auto_show(True, "tablet mode on, no external keyboard: auto-show active")
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

    def start_input_monitor(self):
        """Refresh when input devices come or go; if the monitor stops, quit so systemd restarts the watcher."""
        try:
            self.udev_proc = Gio.Subprocess.new(UDEV_MONITOR, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as err:
            return self.monitor_failed(err.message)
        self.udev_lines = Gio.DataInputStream.new(self.udev_proc.get_stdout_pipe())
        self.udev_lines.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_udev_line)

    def on_udev_line(self, _source, result):
        try:
            line, _length = self.udev_lines.read_line_finish_utf8(result)
        except GLib.Error as err:
            return self.monitor_failed(err.message)
        if line is None:
            return self.monitor_failed("udevadm monitor ended")
        if " add " in line or " remove " in line:
            self.schedule_refresh()
        self.udev_lines.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_udev_line)

    def schedule_refresh(self):
        if not self.refresh_scheduled:
            self.refresh_scheduled = True
            GLib.timeout_add(1000, self.run_scheduled_refresh)

    def run_scheduled_refresh(self):
        self.refresh_scheduled = False
        self.refresh()
        return GLib.SOURCE_REMOVE

    def monitor_failed(self, reason):
        log(f"z13-onboard-follow: input device monitor stopped: {reason}")
        self.exit_code = 1
        GLib.idle_add(quit_loop, self.loop)


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
    keyboards = read_external_keyboards()
    print(f"external keyboard: {', '.join(keyboards) or 'none'}", flush=True)
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
    watcher.loop = loop
    watcher.subscribe()
    watcher.refresh()
    watcher.start_input_monitor()
    loop.run()
    return watcher.exit_code


if __name__ == "__main__":
    sys.exit(main())
