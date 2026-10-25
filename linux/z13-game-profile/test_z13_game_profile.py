"""Hermetic unit tests for z13-game-profile.py: fake proc and power-supply trees, a fake D-Bus client, a fake clock and a fake scheduler; nothing touches the real bus, /proc, /sys, timers or the home directory."""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "z13-game-profile.py")


def load_module(name):
    loader = importlib.machinery.SourceFileLoader(name, PATH)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = load_module("z13_game_profile")
BusError = helper.BusError
TW_ID = "1142710"
HF_ID = "2420110"
TOTAL_WAR = "Total War: WARHAMMER III"
HOLD_LINE = "hold taken: performance for Total War: WARHAMMER III"
DRY_HOLD_LINE = "would hold: performance for Total War: WARHAMMER III"


def reaper_args(app_id):
    return ["/home/user/.local/share/Steam/ubuntu12_32/reaper", "SteamLaunch", f"AppId={app_id}", "--",
            "/home/user/.local/share/Steam/steamapps/common/SteamLinuxRuntime_sniper/_v2-entry-point",
            "--verb=waitforexitandrun", "--", "/home/user/.local/share/Steam/compatibilitytools.d/proton/proton",
            "waitforexitandrun", "/path/to/Warhammer3.exe"]


def write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


class FakeProc:
    """A fake proc tree: one directory per pid with cmdline and comm files, plus environ when given."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root)

    def add(self, pid, args, comm="reaper", environ=None):
        path = os.path.join(self.root, str(pid))
        os.makedirs(path)
        write_bytes(os.path.join(path, "cmdline"), b"".join(arg.encode() + b"\0" for arg in args))
        write_bytes(os.path.join(path, "comm"), comm.encode() + b"\n")
        if environ is not None:
            write_bytes(os.path.join(path, "environ"), b"".join(item.encode() + b"\0" for item in environ))
        return path

    def remove(self, pid):
        shutil.rmtree(os.path.join(self.root, str(pid)))


class FakeSupplies:
    """A fake power-supply tree: one directory per supply with a type file and an optional online file."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root)

    def add(self, name, kind, online=None):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        write_bytes(os.path.join(path, "type"), kind.encode() + b"\n")
        if online is not None:
            self.set_online(name, online)

    def set_online(self, name, value):
        write_bytes(os.path.join(self.root, name, "online"), value.encode() + b"\n")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeScheduler:
    """Records the repeating poll; run() fires it the scripted number of times, advancing the clock before each."""

    def __init__(self, clock, ticks=0):
        self.clock = clock
        self.ticks = ticks
        self.jobs = []

    def repeat(self, seconds, callback):
        self.jobs.append((seconds, callback))

    def run(self):
        for _ in range(self.ticks):
            for seconds, callback in self.jobs:
                self.clock.advance(seconds)
                callback()


class FakeClient:
    """Stands in for the D-Bus client and models the daemon's holds; calls are recorded and failures are scripted by method key."""

    def __init__(self):
        self.present = True
        self.table = {}
        self.next_cookie = 1
        self.profiles = [{"Profile": "power-saver"}, {"Profile": "balanced"}, {"Profile": "performance"}]
        self.active = "balanced"
        self.holds_raw = None
        self.failures = {}
        self.calls = []
        self.reads = []

    def _check(self, key):
        if key in self.failures:
            raise self.failures[key]

    def daemon_present(self):
        return self.present

    def hold(self, profile, reason):
        self.calls.append(("HoldProfile", profile, reason))
        self._check("hold")
        cookie = self.next_cookie
        self.next_cookie += 1
        self.table[cookie] = {"ApplicationId": helper.APP_ID, "Profile": profile, "Reason": reason}
        return cookie

    def release(self, cookie):
        self.calls.append(("ReleaseProfile", cookie))
        self._check("release")
        if cookie not in self.table:
            raise BusError("no such hold")
        del self.table[cookie]

    def read_holds(self):
        self.reads.append("ActiveProfileHolds")
        self._check("read_holds")
        if self.holds_raw is not None:
            return self.holds_raw
        return list(self.table.values())

    def read_profiles(self):
        self.reads.append("Profiles")
        self._check("read_profiles")
        return list(self.profiles)

    def read_active_profile(self):
        self.reads.append("ActiveProfile")
        self._check("read_active")
        return self.active

    def drop_holds(self):
        """Simulate the daemon forgetting our holds, as after a restart."""
        self.table.clear()


class ProcessScanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proc = FakeProc(os.path.join(tmp.name, "proc"))
        self.games = dict(helper.DEFAULT_GAMES)
        self.uid = os.getuid()

    def scan(self, uid=None):
        return helper.find_running_games(self.games, self.proc.root, self.uid if uid is None else uid)

    def test_reaper_command_line_matches(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.assertEqual(self.scan(), {TW_ID: 100})

    def test_similar_ids_do_not_match(self):
        self.proc.add(101, reaper_args("11427100"))
        self.proc.add(102, reaper_args("114271"))
        self.assertEqual(self.scan(), {})

    def test_steam_launch_argument_is_required(self):
        self.proc.add(103, ["reaper", f"AppId={TW_ID}"])
        self.assertEqual(self.scan(), {})

    def test_environ_matches_for_exe_and_proton_comms(self):
        self.proc.add(300, ["Warhammer3.exe"], comm="Warhammer3.exe", environ=["PATH=/usr/bin", f"SteamAppId={TW_ID}"])
        self.proc.add(301, ["proton"], comm="proton", environ=[f"SteamAppId={HF_ID}"])
        self.assertEqual(self.scan(), {TW_ID: 300, HF_ID: 301})

    def test_environ_similar_id_does_not_match(self):
        self.proc.add(302, ["game.exe"], comm="game.exe", environ=[f"SteamAppId={TW_ID}0"])
        self.assertEqual(self.scan(), {})

    def test_environ_ignored_for_other_comm(self):
        self.proc.add(303, ["bash"], comm="bash", environ=[f"SteamAppId={TW_ID}"])
        self.assertEqual(self.scan(), {})

    def test_comm_without_environ_file_is_ignored(self):
        self.proc.add(304, ["reaper"], comm="reaper")
        self.assertEqual(self.scan(), {})

    def test_pid_without_command_line_is_skipped(self):
        path = os.path.join(self.proc.root, "200")
        os.makedirs(path)
        write_bytes(os.path.join(path, "comm"), b"reaper\n")
        self.proc.add(201, reaper_args(TW_ID))
        self.assertEqual(self.scan(), {TW_ID: 201})

    def test_non_numeric_entries_are_ignored(self):
        self.proc.add("self", reaper_args(TW_ID))
        write_bytes(os.path.join(self.proc.root, "cpuinfo"), b"x\n")
        self.assertEqual(self.scan(), {})

    def test_other_users_processes_are_ignored(self):
        self.proc.add(104, reaper_args(TW_ID))
        self.assertEqual(self.scan(uid=self.uid + 1), {})
        self.assertEqual(self.scan(), {TW_ID: 104})

    def test_reads_are_capped_at_64_kib(self):
        path = self.proc.add(107, ["A" * 70000, "SteamLaunch", f"AppId={TW_ID}"])
        self.assertEqual(self.scan(), {})
        self.assertLessEqual(len(helper.read_limited(os.path.join(path, "cmdline"))), 64 * 1024)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_valid_lines_comments_and_blanks(self):
        entries, messages = helper.parse_config("# games\n\n1234567 Some Game\n  7654321  \n")
        self.assertEqual(entries, [("1234567", "Some Game"), ("7654321", None)])
        self.assertEqual(messages, [])

    def test_malformed_lines_are_skipped_with_a_line_number(self):
        entries, messages = helper.parse_config("abc Name\n12x\n1142710Name\n")
        self.assertEqual(entries, [])
        self.assertEqual(len(messages), 3)
        self.assertTrue(messages[2].startswith("skipped games file line 3"))

    def test_names_replace_defaults_and_unknown_ids_get_a_placeholder(self):
        entries = [(TW_ID, "Renamed"), ("7654321", None), (HF_ID, None)]
        games = helper.merge_games(helper.DEFAULT_GAMES, entries, [])
        self.assertEqual(games, {TW_ID: "Renamed", HF_ID: "Horizon Forbidden West", "7654321": "Steam app 7654321"})

    def test_duplicate_ids_later_name_wins(self):
        entries = [("7654321", "A"), ("7654321", "B"), ("7654321", None)]
        games = helper.merge_games(helper.DEFAULT_GAMES, entries, [])
        self.assertEqual(games["7654321"], "B")

    def test_command_line_ids_are_added_after_the_file(self):
        games = helper.merge_games(helper.DEFAULT_GAMES, [], ["7654321", TW_ID])
        self.assertEqual(list(games), [TW_ID, HF_ID, "7654321"])
        self.assertEqual(games[TW_ID], TOTAL_WAR)

    def test_missing_file_is_silent(self):
        self.assertEqual(helper.load_config(os.path.join(self.tmp, "absent.conf")), ([], []))

    def test_unreadable_file_gives_one_message_and_defaults(self):
        entries, messages = helper.load_config(self.tmp)
        self.assertEqual(entries, [])
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith("cannot read"))

    def test_file_entries_are_loaded(self):
        path = os.path.join(self.tmp, "games.conf")
        write_bytes(path, b"1234567 Some Game\n")
        self.assertEqual(helper.load_config(path), ([("1234567", "Some Game")], []))


class PowerSupplyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "power_supply")

    def supplies(self):
        return FakeSupplies(self.root)

    def test_mains_online_is_ac(self):
        self.supplies().add("AC0", "Mains", online="1")
        self.assertTrue(helper.on_ac_power(self.root))

    def test_mains_offline_is_battery(self):
        self.supplies().add("AC0", "Mains", online="0")
        self.assertFalse(helper.on_ac_power(self.root))

    def test_battery_only_is_battery(self):
        self.supplies().add("BAT0", "Battery", online=None)
        self.assertFalse(helper.on_ac_power(self.root))

    def test_no_supplies_is_ac(self):
        self.supplies()
        self.assertTrue(helper.on_ac_power(self.root))

    def test_missing_root_is_ac(self):
        self.assertTrue(helper.on_ac_power(self.root))

    def test_usb_supply_does_not_count(self):
        supplies = self.supplies()
        supplies.add("AC0", "Mains", online="0")
        supplies.add("ucsi-source", "USB", online="1")
        self.assertFalse(helper.on_ac_power(self.root))

    def test_mains_without_online_file_counts_as_offline(self):
        self.supplies().add("AC0", "Mains")
        self.assertFalse(helper.on_ac_power(self.root))

    def test_any_online_mains_counts(self):
        supplies = self.supplies()
        supplies.add("AC0", "Mains", online="0")
        supplies.add("AC1", "Mains", online="1")
        self.assertTrue(helper.on_ac_power(self.root))

    def test_mains_online_beats_battery(self):
        supplies = self.supplies()
        supplies.add("AC0", "Mains", online="1")
        supplies.add("BAT0", "Battery")
        self.assertTrue(helper.on_ac_power(self.root))


class ServiceCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.proc = FakeProc(os.path.join(self.tmp, "proc"))
        self.supplies = FakeSupplies(os.path.join(self.tmp, "power_supply"))
        self.supplies.add("AC0", "Mains", online="1")
        self.clock = FakeClock()
        self.client = FakeClient()
        self.lines = []
        self.games = dict(helper.DEFAULT_GAMES)
        self.svc = self.service()

    def service(self, dry_run=False):
        return helper.Service(games=self.games, profile="performance", client=self.client, clock=self.clock,
                              out=self.lines.append, dry_run=dry_run, proc_root=self.proc.root,
                              power_root=self.supplies.root, uid=os.getuid())

    def holds(self):
        return [call for call in self.client.calls if call[0] == "HoldProfile"]

    def releases(self):
        return [call for call in self.client.calls if call[0] == "ReleaseProfile"]


class ServiceTests(ServiceCase):
    def test_hold_taken_for_game_on_ac(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.assertEqual(self.holds(), [("HoldProfile", "performance", "Total War: WARHAMMER III is running")])
        self.assertEqual(self.lines, [HOLD_LINE])

    def test_no_hold_without_game(self):
        self.svc.poll()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.lines, [])

    def test_no_hold_on_battery(self):
        self.supplies.set_online("AC0", "0")
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.lines, [])

    def test_charger_unplug_releases_at_once(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.supplies.set_online("AC0", "0")
        self.svc.poll()
        self.assertEqual(self.releases(), [("ReleaseProfile", 1)])
        self.assertEqual(self.lines[-1], "hold released: charger unplugged")

    def test_charger_return_takes_hold_again(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.supplies.set_online("AC0", "0")
        self.svc.poll()
        self.supplies.set_online("AC0", "1")
        self.svc.poll()
        self.assertEqual(len(self.holds()), 2)
        self.assertEqual(self.lines[-1], HOLD_LINE)

    def test_game_exit_releases_only_after_15_seconds_absent(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.proc.remove(100)
        self.svc.poll()
        self.clock.advance(5)
        self.svc.poll()
        self.clock.advance(5)
        self.svc.poll()
        self.assertEqual(self.releases(), [])
        self.clock.advance(5)
        self.svc.poll()
        self.assertEqual(self.releases(), [("ReleaseProfile", 1)])
        self.assertEqual(self.lines[-1], "hold released: game exited")

    def test_game_restart_inside_delay_keeps_the_same_hold(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.proc.remove(100)
        self.svc.poll()
        self.clock.advance(5)
        self.proc.add(101, reaper_args(TW_ID))
        self.svc.poll()
        self.assertEqual(self.releases(), [])
        self.proc.remove(101)
        self.svc.poll()
        self.clock.advance(10)
        self.svc.poll()
        self.assertEqual(self.releases(), [])
        self.clock.advance(5)
        self.svc.poll()
        self.assertEqual(len(self.holds()), 1)
        self.assertEqual(self.releases(), [("ReleaseProfile", 1)])

    def test_missing_hold_is_taken_again(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.client.drop_holds()
        self.svc.poll()
        self.assertEqual(len(self.holds()), 2)
        self.assertEqual(self.lines, [HOLD_LINE, "hold no longer listed in ActiveProfileHolds", HOLD_LINE])

    def test_missing_hold_during_delay_is_not_taken_again(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.proc.remove(100)
        self.svc.poll()
        self.client.drop_holds()
        self.clock.advance(5)
        self.svc.poll()
        self.assertEqual(len(self.holds()), 1)
        self.assertEqual(self.releases(), [])
        self.assertEqual(self.lines, [HOLD_LINE, "hold no longer listed in ActiveProfileHolds"])

    def test_unreadable_holds_are_treated_as_held(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.client.failures["read_holds"] = BusError("timeout")
        self.svc.poll()
        self.svc.poll()
        self.assertEqual(len(self.holds()), 1)
        self.assertEqual(self.releases(), [])
        self.assertEqual(self.lines, [HOLD_LINE, "cannot read ActiveProfileHolds: timeout"])

    def test_unexpected_holds_shape_is_treated_as_held(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.client.holds_raw = "junk"
        self.svc.poll()
        self.assertEqual(len(self.holds()), 1)
        self.assertEqual(self.lines, [HOLD_LINE, "ActiveProfileHolds has an unexpected shape"])

    def test_daemon_missing_is_logged_once_and_polling_continues(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.client.present = False
        self.svc.poll()
        self.svc.poll()
        self.svc.poll()
        self.assertEqual(self.lines, ["power-profiles daemon is not on the bus"])
        self.assertEqual(self.holds(), [])
        self.client.present = True
        self.svc.poll()
        self.assertEqual(self.lines[-1], HOLD_LINE)
        self.assertEqual(len(self.holds()), 1)

    def test_failed_hold_is_logged_once_per_distinct_error(self):
        self.client.failures["hold"] = BusError("denied")
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.svc.poll()
        self.svc.poll()
        self.assertEqual(len(self.holds()), 3)
        self.assertEqual(self.lines, ["HoldProfile failed: denied"])
        self.client.failures["hold"] = BusError("timeout")
        self.svc.poll()
        self.assertEqual(self.lines, ["HoldProfile failed: denied", "HoldProfile failed: timeout"])
        del self.client.failures["hold"]
        self.svc.poll()
        self.assertEqual(self.lines[-1], HOLD_LINE)
        self.assertEqual(len(self.holds()), 5)

    def test_profile_not_listed_is_logged_once_and_nothing_is_held(self):
        self.client.profiles = [{"Profile": "balanced"}]
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.svc.poll()
        self.svc.poll()
        self.assertEqual(self.holds(), [])
        self.assertEqual(self.lines, ["profile performance is not listed by the daemon; nothing to hold"])

    def test_unreadable_profiles_hold_nothing(self):
        self.client.failures["read_profiles"] = BusError("gone")
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.svc.poll()
        self.assertEqual(self.holds(), [])
        self.assertEqual(self.lines, ["cannot read Profiles: gone"])

    def test_first_listed_game_names_the_hold(self):
        self.proc.add(100, reaper_args(HF_ID))
        self.proc.add(101, reaper_args(TW_ID))
        self.svc.poll()
        self.assertEqual(self.holds()[0][2], "Total War: WARHAMMER III is running")

    def test_stop_releases_the_hold_once(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.svc.stop()
        self.svc.stop()
        self.assertEqual(self.releases(), [("ReleaseProfile", 1)])
        self.assertEqual(self.lines[-1], "hold released: service stopping")

    def test_stop_without_hold_calls_nothing(self):
        self.svc.stop()
        self.assertEqual(self.client.calls, [])

    def test_release_failure_is_retried_on_next_poll(self):
        self.proc.add(100, reaper_args(TW_ID))
        self.svc.poll()
        self.proc.remove(100)
        self.svc.poll()
        self.clock.advance(15)
        self.client.failures["release"] = BusError("busy")
        self.svc.poll()
        self.assertEqual(self.lines[-1], "ReleaseProfile failed: busy")
        del self.client.failures["release"]
        self.svc.poll()
        self.assertEqual(self.lines[-1], "hold released: game exited")
        self.assertEqual(len(self.releases()), 2)

    def test_dry_run_logs_once_and_never_holds_or_releases(self):
        svc = self.service(dry_run=True)
        self.proc.add(100, reaper_args(TW_ID))
        svc.poll()
        self.clock.advance(5)
        svc.poll()
        self.clock.advance(5)
        svc.poll()
        self.assertEqual(self.lines, [DRY_HOLD_LINE])
        self.assertEqual(self.client.calls, [])
        self.assertNotIn("ActiveProfileHolds", self.client.reads)
        self.proc.remove(100)
        svc.poll()
        self.clock.advance(15)
        svc.poll()
        self.assertEqual(self.lines, [DRY_HOLD_LINE, "would release: game exited"])
        self.assertEqual(self.client.calls, [])

    def test_dry_run_unplug_is_logged(self):
        svc = self.service(dry_run=True)
        self.proc.add(100, reaper_args(TW_ID))
        svc.poll()
        self.supplies.set_online("AC0", "0")
        svc.poll()
        self.assertEqual(self.lines, [DRY_HOLD_LINE, "would release: charger unplugged"])
        self.assertEqual(self.client.calls, [])

    def test_dry_run_stop_releases_nothing(self):
        svc = self.service(dry_run=True)
        self.proc.add(100, reaper_args(TW_ID))
        svc.poll()
        svc.stop()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.lines[-1], "would release: service stopping")


class MainTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.config = os.path.join(self.tmp, "games.conf")
        write_bytes(self.config, b"# no extra games\n")

    def base(self):
        return ["--config", self.config, "--proc-root", self.proc.root,
                "--power-supply-root", self.supplies.root]

    def run_main(self, args, ticks=0):
        lines = []
        clock = FakeClock()
        scheduler = FakeScheduler(clock, ticks)
        deps = helper.Deps(client_factory=lambda: self.client, clock=clock,
                           scheduler_factory=lambda: scheduler, out=lines.append, uid=os.getuid())
        return helper.main(args, deps), lines

    def test_status_prints_detection_and_takes_no_hold(self):
        self.proc.add(100, reaper_args(TW_ID))
        code, lines = self.run_main(self.base() + ["--status"])
        self.assertEqual(code, 0)
        self.assertIn(f"  {TW_ID} Total War: WARHAMMER III", lines)
        self.assertIn("running games:", lines)
        self.assertIn(f"  {TOTAL_WAR} (pid 100)", lines)
        self.assertIn("on AC: yes", lines)
        self.assertIn("active profile: balanced", lines)
        self.assertIn("holds: none", lines)
        self.assertIn("would do: take a hold for Total War: WARHAMMER III", lines)
        self.assertEqual(self.client.calls, [])

    def test_status_shows_our_hold_and_the_release_plan(self):
        self.client.table[1] = {"ApplicationId": helper.APP_ID, "Profile": "performance", "Reason": "x"}
        code, lines = self.run_main(self.base() + ["--status"])
        self.assertEqual(code, 0)
        self.assertIn("  performance from z13-game-profile (x)", lines)
        self.assertIn("would do: release after the 15 s delay, no game running", lines)
        self.assertEqual(self.client.calls, [])

    def test_status_with_daemon_missing(self):
        self.client.present = False
        code, lines = self.run_main(self.base() + ["--status"])
        self.assertEqual(code, 0)
        self.assertIn("daemon not on the bus", lines)
        self.assertEqual(self.client.calls, [])

    def test_app_id_option_adds_a_game(self):
        code, lines = self.run_main(self.base() + ["--status", "--app-id", "7654321"])
        self.assertEqual(code, 0)
        self.assertIn("  7654321 Steam app 7654321", lines)

    def test_config_file_names_games(self):
        write_bytes(self.config, b"1234567 Some Game\n")
        code, lines = self.run_main(self.base() + ["--status"])
        self.assertEqual(code, 0)
        self.assertIn("  1234567 Some Game", lines)

    def test_explicit_missing_config_exits_2(self):
        missing = os.path.join(self.tmp, "absent.conf")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            self.run_main(["--config", missing, "--status"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("config file not found", err.getvalue())

    def test_default_config_missing_is_silent(self):
        missing = os.path.join(self.tmp, "default.conf")
        with mock.patch.object(helper, "DEFAULT_CONFIG", missing):
            code, lines = self.run_main(["--status", "--proc-root", self.proc.root,
                                         "--power-supply-root", self.supplies.root])
        self.assertEqual(code, 0)
        self.assertFalse(any("games file" in line or "cannot read" in line for line in lines))

    def test_bad_options_exit_2(self):
        for argv in (["--poll-seconds", "0"], ["--app-id", "abc"], ["--no-such-option"]):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
                    self.run_main(argv)
                self.assertEqual(ctx.exception.code, 2)

    def test_help_exits_zero(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            self.run_main(["-h"])
        self.assertEqual(ctx.exception.code, 0)

    def test_main_polls_then_releases_on_shutdown(self):
        self.proc.add(100, reaper_args(TW_ID))
        code, lines = self.run_main(self.base(), ticks=2)
        self.assertEqual(code, 0)
        self.assertEqual(lines, [HOLD_LINE, "hold released: service stopping"])
        self.assertEqual(self.client.calls, [("HoldProfile", "performance", "Total War: WARHAMMER III is running"),
                                             ("ReleaseProfile", 1)])

    def test_dry_run_through_main(self):
        self.proc.add(100, reaper_args(TW_ID))
        code, lines = self.run_main(self.base() + ["--dry-run"], ticks=1)
        self.assertEqual(code, 0)
        self.assertEqual(lines, [DRY_HOLD_LINE, "would release: service stopping"])
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
