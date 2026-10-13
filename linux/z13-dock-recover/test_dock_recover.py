import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent


def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


usb = load("z13-dock-usb-revive.py", "usb_revive")
disp = load("z13-display-restore.py", "display_restore")


# ---------------------------------------------------------------- USB revive: fake sysfs ----

class FakeSysfs:
    """A tiny /sys with a dock root port, two dock USB controllers, and a controller elsewhere."""

    def __init__(self, root):
        self.root = Path(root)
        (self.root / "bus/pci/devices").mkdir(parents=True)
        self.base = self.root / "devices/pci0000:00"
        self.port = self.base / "0000:00:01.2"
        self.bridge = self.port / "0000:61:00.0"
        self.add("0000:63:00.0", self.bridge, "0x0c0330", "D0", "active")
        self.add("0000:64:00.0", self.bridge, "0x0c0330", "D0", "active")
        self.add("0000:65:00.0", self.bridge, "0x020000", "D0", "active")  # Ethernet: not a USB controller
        self.add("0000:c6:00.0", self.base / "0000:00:08.3", "0x0c0330", "D3cold", "active")  # not behind the dock
        (self.bridge / "rescan").write_text("")
        (self.root / "bus/pci/rescan").write_text("")

    def add(self, addr, parent, cls, power, runtime):
        d = parent / addr
        (d / "power").mkdir(parents=True)
        (d / "class").write_text(cls + "\n")
        (d / "power_state").write_text(power + "\n")
        (d / "power/runtime_status").write_text(runtime + "\n")
        (d / "remove").write_text("")
        os.symlink(d, self.root / "bus/pci/devices" / addr)

    def set_state(self, addr, power, runtime="active"):
        d = self.root / "bus/pci/devices" / addr
        (d / "power_state").write_text(power + "\n")
        (d / "power/runtime_status").write_text(runtime + "\n")


class UsbReviveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fs = FakeSysfs(self.tmp.name)
        self.logs = []
        self.writes = []

    def tearDown(self):
        self.tmp.cleanup()

    def reviver(self, heals=True, reappear=3):
        def writer(path, value):
            self.writes.append(os.path.relpath(path, self.tmp.name))
            if heals and path.endswith("rescan"):
                self.fs.set_state("0000:64:00.0", "D0")

        return usb.Reviver(sysfs=self.tmp.name, ports=("0000:00:01.1", "0000:00:01.2"), writer=writer,
                           sleep=lambda s: None, log=self.logs.append, reappear_seconds=reappear)

    def test_only_usb_controllers_behind_the_dock_are_found(self):
        found = usb.dock_usb_controllers(self.tmp.name, ("0000:00:01.1", "0000:00:01.2"))
        self.assertEqual(found, ["0000:63:00.0", "0000:64:00.0"])

    def test_dead_signature(self):
        self.assertTrue(usb.looks_dead({"runtime_status": "active", "power_state": "D3cold"}))
        self.assertFalse(usb.looks_dead({"runtime_status": "active", "power_state": "D0"}))
        self.assertFalse(usb.looks_dead({"runtime_status": "suspended", "power_state": "D3hot"}))

    def test_check_reports_dead_controller(self):
        self.fs.set_state("0000:64:00.0", "D3cold")
        self.assertEqual(self.reviver().check(), 1)
        self.assertTrue(any("0000:64:00.0" in line and "DEAD" in line for line in self.logs))

    def test_check_healthy(self):
        self.assertEqual(self.reviver().check(), 0)

    def test_fix_removes_then_rescans_the_parent_bridge(self):
        self.fs.set_state("0000:64:00.0", "D3cold")
        self.assertEqual(self.reviver().fix(), 0)
        self.assertEqual(self.writes, ["bus/pci/devices/0000:64:00.0/remove", "devices/pci0000:00/0000:00:01.2/0000:61:00.0/rescan"])

    def test_fix_gives_up_after_one_attempt_when_it_does_not_come_back(self):
        self.fs.set_state("0000:64:00.0", "D3cold")
        self.assertEqual(self.reviver(heals=False, reappear=2).fix(), 1)
        self.assertEqual(len(self.writes), 2)
        self.assertTrue(any("replug" in line for line in self.logs))

    def test_fix_leaves_healthy_controllers_alone(self):
        self.assertEqual(self.reviver().fix(), 0)
        self.assertEqual(self.writes, [])

    def test_no_dock_is_not_an_error(self):
        empty = tempfile.TemporaryDirectory()
        (Path(empty.name) / "bus/pci/devices").mkdir(parents=True)
        r = usb.Reviver(sysfs=empty.name, writer=lambda p, v: self.fail("must not write"), sleep=lambda s: None, log=self.logs.append)
        self.assertEqual(r.fix(), 0)
        empty.cleanup()


# ------------------------------------------------------------ display restore: fake EDIDs ----

def make_edid(mfg, product, serial_num, name, serial_text):
    b = bytearray(128)
    b[0:8] = bytes([0, 255, 255, 255, 255, 255, 255, 0])
    m = ((ord(mfg[0]) - 64) << 10) | ((ord(mfg[1]) - 64) << 5) | (ord(mfg[2]) - 64)
    b[8], b[9] = m >> 8, m & 0xFF
    b[10], b[11] = product & 0xFF, product >> 8
    b[12:16] = serial_num.to_bytes(4, "little")
    for off, tag, text in ((54, 0xFC, name), (72, 0xFF, serial_text)):
        b[off:off + 5] = bytes([0, 0, 0, tag, 0])
        b[off + 5:off + 18] = (text + "\n").ljust(13).encode("ascii")[:13]
    return bytes(b)


DELL = make_edid("DEL", 0xA07B, 0x01020304, "DELL U2412M", "DELLSERIAL01")
SAMSUNG = make_edid("SAM", 0x0CCB, 0x05060708, "U28E850", "SAMSERIAL01")
LG = make_edid("GSM", 0x774F, 0x0009000A, "LG HDR 4K", "LGSERIAL001")
LAPTOP = make_edid("TMA", 0x0803, 0, "TL134ADXP03", "")


def make_drm(root, connectors):
    for conn, status, edid in connectors:
        d = Path(root) / ("card1-" + conn)
        d.mkdir(parents=True)
        (d / "status").write_text(status + "\n")
        if edid is not None:
            (d / "edid").write_bytes(edid)


def kout(name, mode_id, modes, pos, rot, scale, prio, enabled=True, connected=True):
    return {"name": name, "enabled": enabled, "connected": connected, "currentModeId": mode_id,
            "modes": [{"id": i, "name": n} for i, n in modes], "pos": {"x": pos[0], "y": pos[1]},
            "rotation": rot, "scale": scale, "priority": prio}


M_DELL = [("1", "1920x1200@60"), ("2", "1920x1080@60")]
M_4K = [("1", "3840x2160@60"), ("2", "1920x1080@60")]
M_LAPTOP = [("1", "2560x1600@180")]

# The layout KDE should keep: Dell left, LG primary in the middle, Samsung rotated on the right, laptop below.
SAVED = [
    {"id": "GSM-774f-LGSERIAL001", "label": "LG HDR 4K", "mode": "3840x2160@60", "position": [1920, 306], "rotation": "none", "scale": 1.45, "priority": 1},
    {"id": "TMA-0803-00000000", "label": "TL134ADXP03", "mode": "2560x1600@180", "position": [213, 1506], "rotation": "none", "scale": 1.5, "priority": 2},
    {"id": "SAM-0ccb-SAMSERIAL01", "label": "U28E850", "mode": "3840x2160@60", "position": [4569, 0], "rotation": "left", "scale": 1.65, "priority": 3},
    {"id": "DEL-a07b-DELLSERIAL01", "label": "DELL U2412M", "mode": "1920x1200@60", "position": [0, 306], "rotation": "none", "scale": 1, "priority": 4},
]


def broken_kscreen():
    """After the dock replug: connectors swapped (Dell on DP-6, Samsung on DP-7), both stacked at 4356,0, rotation lost."""
    return {"outputs": [
        kout("eDP-1", "1", M_LAPTOP, (0, 0), 1, 1.5, 1),
        kout("HDMI-A-1", "1", M_4K, (1707, 0), 1, 1.45, 2),
        kout("DP-7", "1", M_4K, (4356, 0), 1, 1.0, 3),
        kout("DP-6", "1", M_DELL, (4356, 0), 1, 1, 4),
    ]}


class DisplayRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_drm(self.tmp.name, [("eDP-1", "connected", LAPTOP), ("HDMI-A-1", "connected", LG),
                                 ("DP-6", "connected", DELL), ("DP-7", "connected", SAMSUNG)])
        self.layout = os.path.join(self.tmp.name, "layout.json")
        with open(self.layout, "w") as f:
            json.dump({"version": 1, "monitors": SAVED}, f)
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_edid_identity(self):
        self.assertEqual(disp.parse_edid(DELL), {"id": "DEL-a07b-DELLSERIAL01", "name": "DELL U2412M"})
        # The laptop panel has no serial number, so the identity falls back to the numeric serial (0), as on the real machine.
        self.assertEqual(disp.parse_edid(LAPTOP)["id"], "TMA-0803-00000000")

    def test_not_an_edid(self):
        self.assertIsNone(disp.parse_edid(b"hello"))

    def test_connected_monitors_skips_disconnected_and_missing_edid(self):
        root = tempfile.TemporaryDirectory()
        make_drm(root.name, [("DP-1", "disconnected", DELL), ("DP-2", "connected", None), ("DP-3", "connected", LG)])
        self.assertEqual(list(disp.connected_monitors(root.name)), ["DP-3"])
        root.cleanup()

    def test_swapped_connectors_get_the_right_settings(self):
        idmap = disp.connected_monitors(self.tmp.name)
        kscreen = broken_kscreen()
        current = disp.snapshot(kscreen, idmap)
        args = disp.plan_args(SAVED, current, kscreen)
        self.assertIn("output.DP-6.position.0,306", args)       # the Dell is on DP-6 now
        self.assertIn("output.DP-7.position.4569,0", args)      # the Samsung is on DP-7 now
        self.assertIn("output.DP-7.rotation.left", args)
        self.assertIn("output.DP-7.scale.1.65", args)
        self.assertIn("output.HDMI-A-1.position.1920,306", args)
        self.assertIn("output.HDMI-A-1.priority.1", args)
        self.assertIn("output.DP-6.priority.4", args)
        self.assertNotIn("output.DP-6.rotation.left", args)
        # Only what differs is sent: no mode changes (all modes already right) and nothing for the Dell's rotation or scale.
        self.assertFalse([a for a in args if ".mode." in a])
        self.assertFalse([a for a in args if a.startswith("output.DP-6.") and (".rotation." in a or ".scale." in a)])

    def test_priorities_untouched_when_only_a_position_differs(self):
        idmap = disp.connected_monitors(self.tmp.name)
        kscreen = {"outputs": [
            kout("eDP-1", "1", M_LAPTOP, (213, 1506), 1, 1.5, 2),
            kout("HDMI-A-1", "1", M_4K, (1920, 306), 1, 1.45, 1),
            kout("DP-7", "1", M_4K, (4569, 0), 2, 1.65, 3),
            kout("DP-6", "1", M_DELL, (10, 306), 1, 1, 4),
        ]}
        args = disp.plan_args(SAVED, disp.snapshot(kscreen, idmap), kscreen)
        self.assertEqual(args, ["output.DP-6.position.0,306"])

    def test_differences_found_then_none_after_fixing(self):
        idmap = disp.connected_monitors(self.tmp.name)
        current = disp.snapshot(broken_kscreen(), idmap)
        self.assertGreater(len(disp.differences(SAVED, current)), 0)
        good = {"outputs": [
            kout("eDP-1", "1", M_LAPTOP, (213, 1506), 1, 1.5, 2),
            kout("HDMI-A-1", "1", M_4K, (1920, 306), 1, 1.45, 1),
            kout("DP-7", "1", M_4K, (4569, 0), 2, 1.65, 3),
            kout("DP-6", "1", M_DELL, (0, 306), 1, 1, 4),
        ]}
        self.assertEqual(disp.differences(SAVED, disp.snapshot(good, idmap)), [])

    def test_apply_runs_kscreen_doctor_once(self):
        calls = []
        args = disp.apply_layout(self.layout, self.tmp.name, kscreen_reader=broken_kscreen, runner=lambda a: calls.append(a), log=self.logs.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], args)
        self.assertTrue(any("rotation" in line for line in self.logs))

    def test_dry_run_does_not_run(self):
        calls = []
        args = disp.apply_layout(self.layout, self.tmp.name, kscreen_reader=broken_kscreen, runner=lambda a: calls.append(a), dry_run=True, log=self.logs.append)
        self.assertTrue(args)
        self.assertEqual(calls, [])

    def test_nothing_to_do_when_layout_matches(self):
        idmap = disp.connected_monitors(self.tmp.name)
        good = {"outputs": [
            kout("eDP-1", "1", M_LAPTOP, (213, 1506), 1, 1.5, 2),
            kout("HDMI-A-1", "1", M_4K, (1920, 306), 1, 1.45, 1),
            kout("DP-7", "1", M_4K, (4569, 0), 2, 1.65, 3),
            kout("DP-6", "1", M_DELL, (0, 306), 1, 1, 4),
        ]}
        calls = []
        self.assertEqual(disp.apply_layout(self.layout, self.tmp.name, kscreen_reader=lambda: good, runner=lambda a: calls.append(a), log=self.logs.append), [])
        self.assertEqual(calls, [])

    def test_fewer_than_two_saved_monitors_present_leaves_layout_alone(self):
        only = tempfile.TemporaryDirectory()
        make_drm(only.name, [("eDP-1", "connected", LAPTOP)])
        kscreen = {"outputs": [kout("eDP-1", "1", M_LAPTOP, (0, 0), 1, 1.5, 1)]}
        calls = []
        self.assertEqual(disp.apply_layout(self.layout, only.name, kscreen_reader=lambda: kscreen, runner=lambda a: calls.append(a), log=self.logs.append), [])
        self.assertEqual(calls, [])
        only.cleanup()

    def test_missing_monitor_renumbers_priorities(self):
        three = tempfile.TemporaryDirectory()
        make_drm(three.name, [("eDP-1", "connected", LAPTOP), ("HDMI-A-1", "connected", LG), ("DP-6", "connected", DELL)])
        kscreen = {"outputs": [
            kout("eDP-1", "1", M_LAPTOP, (0, 0), 1, 1.5, 1),
            kout("HDMI-A-1", "1", M_4K, (1707, 0), 1, 1.45, 2),
            kout("DP-6", "1", M_DELL, (4356, 0), 1, 1, 3),
        ]}
        idmap = disp.connected_monitors(three.name)
        args = disp.plan_args(SAVED, disp.snapshot(kscreen, idmap), kscreen)
        prios = sorted(a.rsplit(".", 1)[1] for a in args if ".priority." in a)
        self.assertEqual(prios, ["1", "2", "3"])
        three.cleanup()

    def test_save_writes_identities(self):
        out = os.path.join(self.tmp.name, "saved.json")
        self.assertEqual(disp.save_layout(out, self.tmp.name, kscreen_reader=broken_kscreen, log=self.logs.append), 0)
        ids = {m["id"] for m in json.load(open(out))["monitors"]}
        self.assertIn("DEL-a07b-DELLSERIAL01", ids)
        self.assertIn("SAM-0ccb-SAMSERIAL01", ids)


class WatcherTests(unittest.TestCase):
    def make(self):
        self.t = 100.0
        self.sig = (("DP-6", "A"), ("DP-7", "B"))
        self.wake = 0.0
        self.applied = 0
        self.logs = []

        def apply():
            self.applied += 1

        return disp.Watcher(lambda: self.sig, apply, now=lambda: self.t, wake_clock=lambda: self.wake, sleep=lambda s: None, log=self.logs.append)

    def run_for(self, w, seconds, step=2.0):
        end = self.t + seconds
        while self.t < end:
            w.step()
            self.t += step

    def test_applies_once_at_startup_after_settling(self):
        w = self.make()
        self.run_for(w, 30)
        self.assertEqual(self.applied, 1)

    def test_connector_swap_triggers_one_apply(self):
        w = self.make()
        self.run_for(w, 30)
        self.sig = (("DP-6", "B"), ("DP-7", "A"))
        self.run_for(w, 30)
        self.assertEqual(self.applied, 2)

    def test_flapping_waits_until_stable(self):
        w = self.make()
        self.run_for(w, 30)
        before = self.applied
        for i in range(5):
            self.sig = (("DP-6", "A"),) if i % 2 == 0 else (("DP-6", "A"), ("DP-7", "B"))
            w.step()
            self.t += 2
        self.assertEqual(self.applied, before)
        self.run_for(w, 30)
        self.assertEqual(self.applied, before + 1)

    def test_wake_triggers_apply_even_if_monitors_look_the_same(self):
        w = self.make()
        self.run_for(w, 30)
        self.wake += 28000
        self.run_for(w, 30)
        self.assertEqual(self.applied, 2)

    def test_quiet_when_nothing_changes(self):
        w = self.make()
        self.run_for(w, 30)
        self.run_for(w, 120)
        self.assertEqual(self.applied, 1)


if __name__ == "__main__":
    unittest.main()
