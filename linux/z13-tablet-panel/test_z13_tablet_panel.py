"""Hermetic unit tests for z13-tablet-panel.py: a fake session bus that answers only the two allowed plasmashell scripts, a fake scheduler, and temporary directories for the drm tree, the appletsrc file and the state file; nothing touches the real bus, plasmashell, /sys, the home directory or any timer."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import unittest

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "z13-tablet-panel.py")


def load_module(name):
    loader = importlib.machinery.SourceFileLoader(name, PATH)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = load_module("z13_tablet_panel")
BusError = helper.BusError
PanelError = helper.PanelError
READ_FORM = re.compile(r"var p=panelById\((\d+)\); print\(p\.height\)")
WRITE_FORM = re.compile(r"panelById\((\d+)\)\.height = (\d+)")
FORBIDDEN = "panels("
PLASMA_LINE = "org.kde.plasmashell is not on the bus; doing nothing until it appears"

REALISTIC_APPLETSRC = """[ActionPlugins][0]
RightButton;NoModifier=org.kde.contextmenu
wheel:Vertical;NoModifier=org.kde.switchdesktop

[ActionPlugins][1]
RightButton;NoModifier=org.kde.contextmenu

[Containments][1]
activityId=
formfactor=0
immutability=1
lastScreen=0
location=0
plugin=org.kde.plasma.folder
wallpaperplugin=org.kde.image

[Containments][1][Wallpaper][org.kde.image][General]
Image=file:///usr/share/wallpapers/Next/contents/images/1920x1080.png

[Containments][7]
formfactor=0
lastScreen=0
plugin=org.kde.desktopcontainment

[Containments][23]
activityId=
formfactor=2
immutability=1
lastScreen=0
location=4
plugin=org.kde.panel
wallpaperplugin=org.kde.description

[Containments][23][Applets][24]
immutability=1
plugin=org.kde.plasma.kickoff

[Containments][23][Applets][24][Configuration][General]
icon=start-here-kde

[Containments][23][Applets][25]
immutability=1
plugin=org.kde.plasma.icontasks

[Containments][23][Applets][25][Configuration][General]
launchers=applications:org.kde.dolphin.desktop

[Containments][23][Applets][30]
plugin=org.kde.panel
lastScreen=0

[Containments][41]
activityId=
formfactor=2
lastScreen=1
location=4
plugin=org.kde.panel
wallpaperplugin=org.kde.description

[Containments][41][Applets][42]
plugin=org.kde.plasma.systemtray

[Containments][57]
formfactor=2
lastScreen=2
location=3
plugin=org.kde.panel

[Containments][60]
formfactor=2
lastScreen=3
location=1
plugin=org.kde.panel

[ScreenMapping]
itemsOrder=0;1

[Updates]
ShowCount=0
"""


def write_file(path, text):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def read_file(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class FakeBus:
    """Stands in for the session bus: KWin's tabletMode, the names that have owners, and plasmashell's panel heights.

    evaluate() accepts only the two allowed plasmashell scripts and records every script it is sent.
    """

    def __init__(self, tablet=True, plasmashell=True, heights=None):
        self.tablet = tablet
        self.owners = {helper.KWIN: True, helper.PLASMASHELL: plasmashell}
        self.heights = {23: 30} if heights is None else dict(heights)
        self.scripts = []
        self.writes = []
        self.fail_writes = 0
        self.ignore_writes = 0
        self.before_write = None
        self.subscribe_error = None
        self.subscribed = False

    def writes_attempted(self):
        return [script for script in self.scripts if WRITE_FORM.fullmatch(script)]

    def read_tablet_mode(self):
        if isinstance(self.tablet, BusError):
            raise self.tablet
        return self.tablet

    def name_has_owner(self, name):
        return self.owners.get(name, False)

    def subscribe(self, on_tablet_changed, on_name_appeared):
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscribed = True

    def evaluate(self, script):
        self.scripts.append(script)
        if not self.owners.get(helper.PLASMASHELL):
            raise BusError("org.kde.plasmashell is not running")
        read = READ_FORM.fullmatch(script)
        if read:
            panel = int(read.group(1))
            if panel not in self.heights:
                return "ReferenceError: panel is gone"
            return f"{self.heights[panel]}\n"
        write = WRITE_FORM.fullmatch(script)
        if write is None:
            raise BusError(f"not an allowed script: {script!r}")
        panel, height = int(write.group(1)), int(write.group(2))
        if self.before_write is not None:
            self.before_write()
        if self.fail_writes:
            self.fail_writes -= 1
            raise BusError("write refused")
        if self.ignore_writes:
            self.ignore_writes -= 1
            return ""
        self.heights[panel] = height
        self.writes.append((panel, height))
        return ""


class FakeScheduler:
    """Records the timers instead of waiting for them; fire_once() runs the pending settle callbacks."""

    def __init__(self):
        self.once_calls = []
        self.repeats = []
        self.on_run = None

    def once(self, seconds, callback):
        self.once_calls.append((seconds, callback))

    def repeat(self, seconds, callback):
        self.repeats.append((seconds, callback))

    def run(self):
        if self.on_run is not None:
            self.on_run()

    def fire_once(self):
        pending, self.once_calls = self.once_calls, []
        for _seconds, callback in pending:
            callback()


class PanelTestCase(unittest.TestCase):
    """Shared fixtures: a temporary tree with one connected laptop screen, a fake bus with laptop panel 23 at height 30, and a list of output lines."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.drm = os.path.join(self.root, "drm")
        self.appletsrc = os.path.join(self.root, "appletsrc")
        self.state_file = os.path.join(self.root, "state", "state.json")
        self.set_screens(1)
        write_file(self.appletsrc, REALISTIC_APPLETSRC)
        self.lines = []
        self.buses = []
        self.scheduler = FakeScheduler()
        self.bus = self.new_bus()

    def tearDown(self):
        for bus in self.buses:
            for script in bus.scripts:
                self.assertNotIn(FORBIDDEN, script)
                self.assertTrue(READ_FORM.fullmatch(script) or WRITE_FORM.fullmatch(script), script)
        self._tmp.cleanup()

    def new_bus(self, **kwargs):
        bus = FakeBus(**kwargs)
        self.buses.append(bus)
        return bus

    def set_screens(self, count):
        """Make the drm tree show count connected screens, plus disconnected and non-connector entries."""
        shutil.rmtree(self.drm, ignore_errors=True)
        for index, name in enumerate(("card0-eDP-1", "card0-DP-1", "card1-HDMI-A-1")):
            write_file(os.path.join(self.drm, name, "status"), "connected\n" if index < count else "disconnected\n")
        write_file(os.path.join(self.drm, "card0", "status"), "connected\n")
        write_file(os.path.join(self.drm, "renderD128", "status"), "connected\n")

    def config(self, **overrides):
        values = dict(tablet_height=48, appletsrc=self.appletsrc, state_file=self.state_file,
                      drm_root=self.drm, poll_seconds=10, dry_run=False)
        values.update(overrides)
        return helper.Config(**values)

    def service(self, config=None, bus=None):
        return helper.Service(config or self.config(), bus or self.bus, self.scheduler, self.lines.append)

    def common_args(self):
        return ["--appletsrc", self.appletsrc, "--state-file", self.state_file, "--drm-root", self.drm]

    def run_main(self, *argv, bus=None):
        deps = helper.Deps(lambda: bus or self.bus, lambda: self.scheduler, self.lines.append)
        return helper.main(list(argv), deps)


class ScreenCountTests(PanelTestCase):
    def test_only_connected_card_connectors_are_counted(self):
        root = os.path.join(self.root, "drm-count")
        write_file(os.path.join(root, "card0-eDP-1", "status"), "connected\n")
        write_file(os.path.join(root, "card0-DP-1", "status"), "disconnected\n")
        write_file(os.path.join(root, "card1-HDMI-A-1", "status"), "connected\n")
        write_file(os.path.join(root, "card0", "status"), "connected\n")
        write_file(os.path.join(root, "renderD128", "status"), "connected\n")
        os.makedirs(os.path.join(root, "card2-DP-9"))
        self.assertEqual(helper.count_connected(root), 2)

    def test_missing_root_counts_as_no_screens(self):
        self.assertEqual(helper.count_connected(os.path.join(self.root, "absent")), 0)

    def test_set_screens_counts_for_the_fixture(self):
        self.set_screens(2)
        self.assertEqual(helper.count_connected(self.drm), 2)


class ParsingTests(PanelTestCase):
    def test_laptop_panel_is_found_in_the_realistic_file(self):
        self.assertEqual(helper.detect_panel(REALISTIC_APPLETSRC), (23, None))

    def test_panels_on_other_screens_and_nested_sections_are_not_counted(self):
        self.assertEqual(helper.parse_panel_ids(REALISTIC_APPLETSRC), [23])

    def test_two_panels_on_screen_zero_are_ambiguous(self):
        text = REALISTIC_APPLETSRC + "\n[Containments][58]\nlastScreen=0\nplugin=org.kde.panel\n"
        panel, problem = helper.detect_panel(text)
        self.assertIsNone(panel)
        self.assertEqual(problem, "2 panels on screen 0 in the appletsrc file (23, 58)")

    def test_no_panel_on_screen_zero_is_refused(self):
        panel, problem = helper.detect_panel("[Containments][41]\nlastScreen=1\nplugin=org.kde.panel\n")
        self.assertIsNone(panel)
        self.assertEqual(problem, "no panel on screen 0 in the appletsrc file")

    def test_non_integer_ids_are_rejected(self):
        text = ("[Containments][abc]\nlastScreen=0\nplugin=org.kde.panel\n"
                "[Containments][1x]\nlastScreen=0\nplugin=org.kde.panel\n"
                "[Containments][-2]\nlastScreen=0\nplugin=org.kde.panel\n"
                "[Containments][ 3]\nlastScreen=0\nplugin=org.kde.panel\n"
                "[Containments][٣]\nlastScreen=0\nplugin=org.kde.panel\n")
        self.assertEqual(helper.parse_panel_ids(text), [])
        self.assertIsNone(helper.detect_panel(text)[0])

    def test_long_file_with_duplicate_sections_still_finds_one_panel(self):
        text = "".join(f"[Containments][{number}]\nlastScreen=1\nplugin=org.kde.panel\n\n"
                        for number in range(100, 4100))
        text += "[Containments][23]\nlastScreen=0\nplugin=org.kde.panel\n" * 2
        self.assertEqual(helper.detect_panel(text), (23, None))

    def test_unparseable_file_gives_a_message(self):
        panel, problem = helper.detect_panel("plugin=org.kde.panel\n")
        self.assertIsNone(panel)
        self.assertTrue(problem.startswith("cannot parse the appletsrc file"))


class ScriptTests(PanelTestCase):
    def test_the_two_forms_are_exact(self):
        self.assertEqual(helper.read_script(23), "var p=panelById(23); print(p.height)")
        self.assertEqual(helper.write_script(23, 48), "panelById(23).height = 48")
        self.assertNotIn(FORBIDDEN, helper.read_script(23))
        self.assertNotIn(FORBIDDEN, helper.write_script(23, 48))

    def test_bad_ids_and_heights_are_refused(self):
        with self.assertRaises(ValueError):
            helper.read_script("23")
        with self.assertRaises(ValueError):
            helper.read_script(True)
        with self.assertRaises(ValueError):
            helper.write_script("23; x", 48)
        with self.assertRaises(ValueError):
            helper.write_script(23, 500)
        with self.assertRaises(ValueError):
            helper.write_script(23, "48")

    def test_height_replies_are_parsed_strictly(self):
        self.assertEqual(helper.parse_height("30\n"), 30)
        for text in ("", "abc", "30.5", "0", "201", "-5", "ReferenceError: x", None):
            with self.subTest(text=text), self.assertRaises(PanelError):
                helper.parse_height(text)


class StateTests(PanelTestCase):
    def test_saved_state_round_trips(self):
        helper.save_state(self.state_file, 23, 30)
        self.assertEqual(helper.load_state(self.state_file), (helper.State(23, 30), None))

    def test_missing_state_is_absent_not_a_problem(self):
        self.assertEqual(helper.load_state(self.state_file), (None, None))

    def test_bad_state_is_refused(self):
        bad = ['{"panel_id": ', "[]", '{"panel_id": "23", "original_height": 30}',
               '{"panel_id": 23, "original_height": 500}', '{"panel_id": true, "original_height": 30}']
        for text in bad:
            with self.subTest(text=text):
                write_file(self.state_file, text)
                state, problem = helper.load_state(self.state_file)
                self.assertIsNone(state)
                self.assertIsNotNone(problem)

    def test_save_never_overwrites_an_existing_state(self):
        helper.save_state(self.state_file, 23, 30)
        with self.assertRaises(FileExistsError):
            helper.save_state(self.state_file, 23, 99)
        self.assertEqual(helper.load_state(self.state_file)[0], helper.State(23, 30))


class ServiceTests(PanelTestCase):
    def test_tablet_on_one_screen_saves_the_original_before_the_first_resize(self):
        seen = []
        self.bus.before_write = lambda: seen.append(os.path.exists(self.state_file))
        self.service().start()
        self.assertEqual(seen, [True])
        self.assertEqual(self.bus.heights[23], 48)
        self.assertEqual(self.bus.writes_attempted(), ["panelById(23).height = 48"])
        self.assertEqual(json.loads(read_file(self.state_file)), {"panel_id": 23, "original_height": 30})
        self.assertEqual(self.lines, ["tablet mode on: panel 23 set to 48; original height 30 is in the state file"])

    def test_tablet_on_two_screens_does_nothing(self):
        self.set_screens(2)
        self.service().evaluate()
        self.assertEqual(self.bus.scripts, [])
        self.assertFalse(os.path.exists(self.state_file))
        self.assertEqual(self.lines, ["tablet mode on but 2 screens are connected: the panel is left alone"])

    def test_tablet_off_without_state_does_nothing(self):
        self.bus.tablet = False
        self.service().evaluate()
        self.assertEqual(self.bus.scripts, [])
        self.assertEqual(self.lines, ["tablet mode off: nothing to restore"])

    def test_tablet_off_restores_the_height_and_deletes_the_state(self):
        helper.save_state(self.state_file, 23, 30)
        self.bus.heights[23] = 48
        self.bus.tablet = False
        self.service().evaluate()
        self.assertEqual(self.bus.heights[23], 30)
        self.assertFalse(os.path.exists(self.state_file))
        self.assertEqual(self.lines, ["tablet mode off: restored panel 23 to 30"])

    def test_saved_original_survives_a_restart_and_is_not_overwritten(self):
        self.service().evaluate()
        first = read_file(self.state_file)
        self.bus.heights[23] = 30
        self.service().evaluate()
        self.assertEqual(self.bus.heights[23], 48)
        self.assertEqual(read_file(self.state_file), first)
        self.assertEqual(self.bus.writes_attempted(), ["panelById(23).height = 48"] * 2)

    def test_existing_state_is_never_overwritten_by_a_resize(self):
        helper.save_state(self.state_file, 23, 25)
        self.service().evaluate()
        self.assertEqual(self.bus.heights[23], 48)
        self.assertEqual(json.loads(read_file(self.state_file)), {"panel_id": 23, "original_height": 25})

    def test_crash_recovery_restores_on_startup(self):
        helper.save_state(self.state_file, 23, 30)
        self.bus.heights[23] = 48
        self.bus.tablet = False
        self.service().start()
        self.assertEqual(self.bus.heights[23], 30)
        self.assertFalse(os.path.exists(self.state_file))
        self.assertEqual(self.lines[0], "tablet mode off: restored panel 23 to 30")

    def test_panel_already_tablet_sized_without_state_is_left_alone(self):
        self.bus.heights[23] = 48
        self.service().evaluate()
        self.assertEqual(self.bus.writes_attempted(), [])
        self.assertFalse(os.path.exists(self.state_file))
        self.assertEqual(self.lines, ["tablet mode on: panel 23 is already at 48; nothing saved"])

    def test_plasmashell_missing_logs_once_and_retries_after_it_appears(self):
        bus = self.new_bus(plasmashell=False)
        svc = self.service(bus=bus)
        svc.evaluate()
        svc.evaluate()
        svc.evaluate()
        self.assertEqual(self.lines, [PLASMA_LINE])
        self.assertEqual(bus.scripts, [])
        bus.owners[helper.PLASMASHELL] = True
        svc.on_name_appeared(helper.PLASMASHELL)
        self.scheduler.fire_once()
        self.assertEqual(bus.heights[23], 48)

    def test_name_appearance_waits_for_the_settle_delay(self):
        svc = self.service()
        svc.on_name_appeared(helper.KWIN)
        svc.on_name_appeared(helper.PLASMASHELL)
        self.assertEqual(self.bus.scripts, [])
        self.assertEqual([seconds for seconds, _ in self.scheduler.once_calls], [helper.SETTLE_SECONDS])
        self.scheduler.fire_once()
        self.assertEqual(self.bus.heights[23], 48)

    def test_tablet_mode_signal_evaluates_at_once(self):
        self.service().on_tablet_changed()
        self.assertEqual(self.bus.heights[23], 48)
        self.assertEqual(self.scheduler.once_calls, [])

    def test_start_subscribes_and_polls_at_the_configured_interval(self):
        svc = self.service(self.config(poll_seconds=7))
        svc.start()
        self.assertTrue(self.bus.subscribed)
        self.assertEqual([seconds for seconds, _ in self.scheduler.repeats], [7])

    def test_failed_write_is_logged_once_and_retried(self):
        self.bus.fail_writes = 2
        svc = self.service()
        svc.evaluate()
        svc.evaluate()
        self.assertEqual(self.lines, ["cannot set panel 23 to 48, will retry: write refused"])
        svc.evaluate()
        self.assertEqual(self.bus.heights[23], 48)
        self.assertEqual(self.lines[-1], "tablet mode on: panel 23 set to 48; original height 30 is in the state file")

    def test_failed_verify_is_logged_and_retried(self):
        self.bus.ignore_writes = 1
        svc = self.service()
        svc.evaluate()
        self.assertEqual(self.lines, ["cannot set panel 23 to 48, will retry: panel 23 reads 30 after the write, wanted 48"])
        svc.evaluate()
        self.assertEqual(self.bus.heights[23], 48)

    def test_unreadable_height_is_logged_and_nothing_is_written(self):
        self.bus.heights = {}
        self.service().evaluate()
        self.assertEqual(self.bus.writes_attempted(), [])
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("cannot read panel 23 height:"))

    def test_unreadable_tablet_mode_does_nothing(self):
        bus = self.new_bus(tablet=BusError("no KWin"))
        self.service(bus=bus).evaluate()
        self.assertEqual(bus.scripts, [])
        self.assertEqual(self.lines, ["cannot read tabletMode from org.kde.KWin: no KWin"])

    def test_missing_appletsrc_leaves_the_panel_alone(self):
        svc = self.service(self.config(appletsrc=os.path.join(self.root, "missing")))
        svc.evaluate()
        self.assertEqual(self.bus.scripts, [])
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("cannot read "))

    def test_ambiguous_appletsrc_leaves_the_panel_alone(self):
        write_file(self.appletsrc, REALISTIC_APPLETSRC + "\n[Containments][58]\nlastScreen=0\nplugin=org.kde.panel\n")
        self.service().evaluate()
        self.assertEqual(self.bus.scripts, [])
        self.assertEqual(self.lines, ["2 panels on screen 0 in the appletsrc file (23, 58); the panel is left alone"])

    def test_state_for_another_panel_leaves_both_alone(self):
        helper.save_state(self.state_file, 24, 30)
        self.service().evaluate()
        self.assertEqual(self.bus.scripts, [])
        self.assertEqual(self.lines, ["the state file is for panel 24 but the laptop panel is 23; both are left alone"])

    def test_invalid_state_file_is_never_touched(self):
        write_file(self.state_file, "{not json")
        svc = self.service()
        svc.evaluate()
        svc.evaluate()
        self.assertEqual(self.bus.scripts, [])
        self.assertEqual(read_file(self.state_file), "{not json")
        self.assertEqual(len(self.lines), 1)
        self.assertIn("is not valid JSON; the panel is left alone", self.lines[0])

    def test_dry_run_never_writes_and_never_saves_state(self):
        svc = self.service(self.config(dry_run=True))
        svc.evaluate()
        svc.evaluate()
        self.assertEqual(self.bus.writes_attempted(), [])
        self.assertFalse(os.path.exists(self.state_file))
        self.assertEqual(self.bus.heights[23], 30)
        self.assertEqual(self.lines, ["would save height 30 as the original, then set panel 23 to 48"])

    def test_dry_run_restore_keeps_the_state_file(self):
        helper.save_state(self.state_file, 23, 30)
        self.bus.heights[23] = 48
        self.bus.tablet = False
        self.service(self.config(dry_run=True)).evaluate()
        self.assertEqual(self.bus.writes_attempted(), [])
        self.assertEqual(self.bus.heights[23], 48)
        self.assertTrue(os.path.exists(self.state_file))
        self.assertEqual(self.lines, ["would restore panel 23 to 30 and delete the state file"])

    def test_restore_waits_while_two_screens_are_connected(self):
        helper.save_state(self.state_file, 23, 30)
        self.bus.heights[23] = 48
        self.bus.tablet = False
        self.set_screens(2)
        self.service().evaluate()
        self.assertEqual(self.bus.writes_attempted(), [])
        self.assertTrue(os.path.exists(self.state_file))
        self.assertEqual(self.lines, ["2 screens connected: panel 23 keeps its tablet height and the state file is kept until the laptop is the only screen"])

    def test_signal_ends_the_loop_and_restores_the_panel(self):
        helper.save_state(self.state_file, 23, 30)
        self.bus.heights[23] = 48
        self.scheduler.on_run = lambda: None
        self.assertEqual(self.run_main(*self.common_args()), 0)
        self.assertEqual(self.bus.heights[23], 30)
        self.assertFalse(os.path.exists(self.state_file))
        self.assertEqual(self.lines[-1], "service stopping: restored panel 23 to 30")

    def test_stop_with_two_screens_keeps_the_state_file(self):
        helper.save_state(self.state_file, 23, 30)
        self.bus.heights[23] = 48
        self.set_screens(2)
        self.assertEqual(self.run_main(*self.common_args()), 0)
        self.assertEqual(self.bus.heights[23], 48)
        self.assertTrue(os.path.exists(self.state_file))
        self.assertEqual(self.lines[-1], "stopping with 2 screens connected: panel 23 keeps its tablet height and the state file is kept")

    def test_missing_session_bus_exits_1(self):
        bus = self.new_bus()
        bus.subscribe_error = BusError("no session bus")
        self.assertEqual(self.run_main(*self.common_args(), bus=bus), 1)
        self.assertEqual(self.lines, ["cannot reach the session bus: no session bus"])


class StatusTests(PanelTestCase):
    def test_status_with_two_screens_never_queries_or_writes(self):
        self.set_screens(2)
        self.assertEqual(self.run_main("--status", *self.common_args()), 0)
        self.assertEqual(self.bus.scripts, [])
        self.assertEqual(self.lines, [
            "tablet mode: on",
            "connected screens: 2",
            "panel id: 23",
            "state file: absent",
            "panel height: not queried, 2 screens are connected",
            "would do: nothing, 2 screens are connected",
        ])
        self.assertFalse(os.path.exists(self.state_file))

    def test_status_with_one_screen_reads_the_height_and_writes_nothing(self):
        self.assertEqual(self.run_main("--status", *self.common_args()), 0)
        self.assertEqual(self.bus.scripts, [helper.read_script(23)])
        self.assertEqual(self.bus.writes_attempted(), [])
        self.assertIn("panel height: 30", self.lines)
        self.assertEqual(self.lines[-1], "would do: save height 30 as the original, then set panel 23 to 48")
        self.assertFalse(os.path.exists(self.state_file))

    def test_status_with_saved_state_and_tablet_off_describes_the_restore_without_doing_it(self):
        helper.save_state(self.state_file, 23, 30)
        self.bus.heights[23] = 48
        self.bus.tablet = False
        self.assertEqual(self.run_main("--status", *self.common_args()), 0)
        self.assertEqual(self.bus.writes_attempted(), [])
        self.assertEqual(self.bus.heights[23], 48)
        self.assertTrue(os.path.exists(self.state_file))
        self.assertIn("state file: present (panel 23, original height 30)", self.lines)
        self.assertEqual(self.lines[-1], "would do: restore panel 23 to 30 and delete the state file")

    def test_status_reports_an_unreachable_bus_without_failing(self):
        bus = self.new_bus(plasmashell=False)

        def no_bus():
            raise BusError("no session bus")

        bus.read_tablet_mode = no_bus
        self.assertEqual(self.run_main("--status", *self.common_args(), bus=bus), 0)
        self.assertEqual(self.lines[0], "tablet mode: unknown (no session bus)")
        self.assertIn("panel height: not queried, org.kde.plasmashell is not on the bus", self.lines)
        self.assertEqual(bus.scripts, [])


class MainTests(PanelTestCase):
    def test_usage_errors_exit_2(self):
        for argv in (["--tablet-height", "10"], ["--tablet-height", "201"], ["--tablet-height", "abc"],
                     ["--poll-seconds", "0"], ["--no-such-option"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    self.run_main(*argv)
                self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.bus.scripts, [])


if __name__ == "__main__":
    unittest.main()
