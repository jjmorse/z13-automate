#!/usr/bin/env python3
"""Revive a Thunderbolt dock's USB controllers that did not survive sleep.

On the ASUS ROG Flow Z13 with a CalDigit TS3 Plus, a long sleep can leave one of the dock's USB
controllers (a Fresco FL1100) dead after resume: the kernel logs "Controller not ready at resume -19"
and "HC died", and everything on that controller disconnects. The PCI device then reports power
state D3cold while its runtime status still says active. Unplugging and replugging the dock fixes it.
This does the software equivalent: remove the dead controller and rescan its parent bridge.

  z13-dock-usb-revive check          list the dock's USB controllers and whether each looks dead (no root)
  sudo z13-dock-usb-revive fix       revive the ones that look dead (the systemd unit runs this after resume)

Options: --ports 0000:00:01.1,0000:00:01.2   USB4 root ports the dock hangs off (default shown)
         --settle SECONDS                     wait this long before checking (default 4)
         --sysfs PATH                         use another /sys (for tests)
"""
import argparse
import os
import sys
import time

DEFAULT_PORTS = ("0000:00:01.1", "0000:00:01.2")
USB_CLASS_PREFIX = "0x0c03"
REAPPEAR_SECONDS = 15.0


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def write_file(path, value):
    with open(path, "w") as f:
        f.write(value)


def dock_usb_controllers(sysfs, ports):
    """PCI addresses of USB controllers that sit behind one of the dock root ports."""
    devices = os.path.join(sysfs, "bus", "pci", "devices")
    found = []
    try:
        names = sorted(os.listdir(devices))
    except OSError:
        return found
    for addr in names:
        cls = read(os.path.join(devices, addr, "class"))
        if not cls or not cls.startswith(USB_CLASS_PREFIX):
            continue
        parts = os.path.realpath(os.path.join(devices, addr)).split(os.sep)
        if addr in parts and any(p in ports for p in parts[: parts.index(addr)]):
            found.append(addr)
    return found


def device_state(sysfs, addr):
    base = os.path.join(sysfs, "bus", "pci", "devices", addr)
    return {
        "power_state": read(os.path.join(base, "power_state")) or "unknown",
        "runtime_status": read(os.path.join(base, "power", "runtime_status")) or "unknown",
        "driver": os.path.basename(os.path.realpath(os.path.join(base, "driver"))) if os.path.exists(os.path.join(base, "driver")) else "none",
    }


def looks_dead(state):
    """Active in the kernel's eyes but not powered up: the signature seen after a failed resume."""
    return state["runtime_status"] == "active" and state["power_state"] != "D0"


class Reviver:
    def __init__(self, sysfs="/sys", ports=DEFAULT_PORTS, writer=write_file, sleep=time.sleep, log=print, reappear_seconds=REAPPEAR_SECONDS):
        self.sysfs = sysfs
        self.ports = tuple(ports)
        self.writer = writer
        self.sleep = sleep
        self.log = log
        self.reappear_seconds = reappear_seconds

    def survey(self):
        return [(addr, device_state(self.sysfs, addr)) for addr in dock_usb_controllers(self.sysfs, self.ports)]

    def check(self):
        survey = self.survey()
        if not survey:
            self.log("no USB controllers found behind the dock root ports %s (dock not connected?)" % ", ".join(self.ports))
            return 0
        dead = 0
        for addr, st in survey:
            verdict = "DEAD" if looks_dead(st) else "ok"
            if verdict == "DEAD":
                dead += 1
            self.log("%s  power=%-6s runtime=%-9s driver=%-9s %s" % (addr, st["power_state"], st["runtime_status"], st["driver"], verdict))
        return 1 if dead else 0

    def revive(self, addr):
        base = os.path.join(self.sysfs, "bus", "pci", "devices", addr)
        parent = os.path.dirname(os.path.realpath(base))
        rescan = os.path.join(parent, "rescan")
        if not os.path.exists(rescan):
            rescan = os.path.join(self.sysfs, "bus", "pci", "rescan")
        self.log("%s looks dead, removing it and rescanning %s" % (addr, os.path.dirname(rescan) if rescan.startswith(parent) else "the PCI bus"))
        try:
            self.writer(os.path.join(base, "remove"), "1")
        except OSError as e:
            self.log("%s: remove failed: %s" % (addr, e))
            return False
        self.sleep(1)
        try:
            self.writer(rescan, "1")
        except OSError as e:
            self.log("%s: rescan failed: %s" % (addr, e))
            return False
        waited = 0.0
        while waited <= self.reappear_seconds:
            if os.path.exists(base) and not looks_dead(device_state(self.sysfs, addr)) and device_state(self.sysfs, addr)["power_state"] == "D0":
                self.log("%s is back and powered (D0)" % addr)
                return True
            self.sleep(1)
            waited += 1
        self.log("%s did not come back within %d seconds; a replug of the dock is needed" % (addr, self.reappear_seconds))
        return False

    def fix(self, settle=0):
        if settle:
            self.sleep(settle)
        dead = [addr for addr, st in self.survey() if looks_dead(st)]
        if not dead:
            self.log("dock USB controllers look healthy, nothing to do")
            return 0
        failed = [addr for addr in dead if not self.revive(addr)]
        return 1 if failed else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Revive the dock's USB controllers after sleep")
    sub = p.add_subparsers(dest="command", required=True)
    for name, helptext in (("check", "report only"), ("fix", "revive controllers that look dead")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--ports", default=",".join(DEFAULT_PORTS), help="comma-separated USB4 root ports")
        s.add_argument("--sysfs", default="/sys", help=argparse.SUPPRESS)
        s.add_argument("--settle", type=float, default=4.0 if name == "fix" else 0.0, help="seconds to wait before checking")
    args = p.parse_args(argv)
    reviver = Reviver(sysfs=args.sysfs, ports=[x for x in args.ports.split(",") if x])
    if args.command == "check":
        return reviver.check()
    try:
        return reviver.fix(settle=args.settle)
    except PermissionError:
        print("permission denied, run with sudo", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
