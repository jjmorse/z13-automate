"""Hermetic unit tests for z13-claude-idle-sleep.py: fake runners, fake clocks, fake streams and temporary log files only."""

import contextlib
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "z13-claude-idle-sleep.py")


def load_module(name):
    loader = importlib.machinery.SourceFileLoader(name, PATH)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = load_module("z13_claude_idle_sleep")

T0 = datetime.datetime(2026, 10, 21, 20, 0, 0).timestamp()
IDLE = 3600
NOW = T0 + 7200
CLAUDE_APP = helper.CLAUDE_APP
DUE = "[remote-tools-device] rotation due — re-presenting credential in-band"
HELD = "[remote-tools-device] keep-awake held (pss=0)"
QUIET_300 = "[remote-tools-device] keep-awake released (quiet, held 300001ms)"


def stamp(offset):
    return datetime.datetime.fromtimestamp(T0 + offset).strftime("%Y-%m-%d %H:%M:%S")


def line(offset, text, level="info"):
    return f"{stamp(offset)} [{level}] {text}"


def startup():
    return [line(0, "startup message")]


def rotation(start, held_ms=300001, release=True):
    """Return the log lines of one rotation claim held from start, released after held_ms when release is True."""
    lines = [
        line(start, DUE),
        line(start, "[keep-awake] started (id=78, claims=remote_tools)"),
        line(start, HELD),
        line(start, "[remote-tools-device] served get_device_info"),
        line(start, "[remote-tools-device] credential re-verified in-band"),
    ]
    if release:
        end = start + held_ms // 1000
        lines += [
            line(end, "[keep-awake] stopped (id=78): last claim remote_tools released"),
            line(end, f"[remote-tools-device] keep-awake released (quiet, held {held_ms}ms)"),
        ]
    return lines


def rotation_only_log():
    return startup() + rotation(1800) + rotation(3600)


def analyse(lines, now=NOW, read_ok=True):
    return helper.analyse(lines, now, IDLE, read_ok)


def claude_row(what="sleep"):
    return helper.Inhibition(what, CLAUDE_APP, "Electron", "block", 3)


def other_row(app="Steam"):
    return helper.Inhibition("idle", app, "Game streaming", "block", 3)


def inhibit_json(rows):
    return json.dumps({"type": "a(ssssu)", "data": rows})


ACTIVE = helper.Activity(None, [], True)


class FakeTime:
    """Wall and boot-time clocks; boot_offset grows like time spent asleep."""

    def __init__(self, now=NOW):
        self.now = now
        self.boot_offset = 0.0

    def clocks(self):
        return helper.Clocks(lambda: self.now, lambda: self.now + self.boot_offset, lambda: self.now)


class FakeRunner:
    """Answers the busctl inhibition read and the suspend call with scripted results; nothing real runs."""

    def __init__(self, inhibit_stdout=None, inhibit_returncode=0, inhibit_error=None,
                 suspend_returncode=0, suspend_error=None):
        self.inhibit_stdout = inhibit_stdout if inhibit_stdout is not None else inhibit_json([["sleep", CLAUDE_APP, "Electron", "block", 3]])
        self.inhibit_returncode = inhibit_returncode
        self.inhibit_error = inhibit_error
        self.suspend_returncode = suspend_returncode
        self.suspend_error = suspend_error
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if "Suspend" in cmd:
            if self.suspend_error is not None:
                raise self.suspend_error
            return subprocess.CompletedProcess(cmd, self.suspend_returncode, "", "busctl failed")
        if self.inhibit_error is not None:
            raise self.inhibit_error
        return subprocess.CompletedProcess(cmd, self.inhibit_returncode, self.inhibit_stdout, "")

    def suspend_calls(self):
        return [(cmd, kwargs) for cmd, kwargs in self.calls if "Suspend" in cmd]


class FakeSpawn:
    """Records the swayidle command line and hands back a fake process with a scripted stream."""

    def __init__(self, stream):
        self.proc = FakeProc(stream)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        return self.proc


class FakeProc:
    def __init__(self, stream):
        self.stdout = stream
        self.terminated = False

    def terminate(self):
        self.terminated = True


class FakeStream:
    """Plays back raw swayidle chunks, one per read; an empty script reads as end of file."""

    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read_chunk(self):
        return self.chunks.pop(0) if self.chunks else b""


class FakeSelector:
    """Plays back ready and timeout steps; 'interrupt' raises KeyboardInterrupt and an exhausted script reads as ready EOF."""

    def __init__(self, steps, time_source=None):
        self.steps = list(steps)
        self.time_source = time_source
        self.timeouts = []

    def register(self, stream, events):
        self.stream = stream

    def select(self, timeout):
        self.timeouts.append(timeout)
        if not self.steps:
            return [(None, 1)]
        step = self.steps.pop(0)
        if step == "interrupt":
            raise KeyboardInterrupt
        if step:
            return [(None, 1)]
        if self.time_source is not None:
            self.time_source.now += timeout
        return []


def make_deps(runner, out, clock, spawn=None, which=None, selector=None):
    return helper.Deps(
        run=runner,
        spawn=spawn or FakeSpawn(FakeStream([])),
        which=which or (lambda name: "/usr/bin/swayidle"),
        clocks=clock.clocks(),
        selector_factory=lambda: selector or FakeSelector([]),
        out=out,
        reader=lambda stream: stream.read_chunk,
    )


def write_log(directory, name, lines):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name


class LogAnalysisTest(unittest.TestCase):
    def test_rotation_only_log_has_no_activity(self):
        activity = analyse(rotation_only_log())
        self.assertIsNone(activity.last_real)
        self.assertEqual(activity.open_holds, [])
        self.assertTrue(activity.history_ok)

    def test_rotation_hold_of_301_seconds_is_rotation(self):
        activity = analyse(startup() + rotation(0, held_ms=301000))
        self.assertIsNone(activity.last_real)

    def test_hold_of_420_seconds_is_activity_at_its_release(self):
        activity = analyse(startup() + rotation(0, held_ms=420000))
        self.assertEqual(activity.last_real, T0 + 420)
        self.assertEqual(activity.open_holds, [])

    def test_ongoing_rotation_under_timeout_is_rotation_only(self):
        activity = analyse(startup() + rotation(0, release=False), now=T0 + 200)
        self.assertIsNone(activity.last_real)
        self.assertEqual(activity.open_holds, [])

    def test_ongoing_rotation_past_timeout_counts_as_activity_now(self):
        activity = analyse(startup() + rotation(0, release=False), now=T0 + 400)
        self.assertEqual(activity.last_real, T0 + 400)
        self.assertEqual(activity.open_holds, [])

    def test_release_after_the_next_hold_does_not_close_the_earlier_hold(self):
        lines = startup() + [
            line(0, DUE), line(0, HELD),
            line(100, DUE), line(100, HELD),
            line(300, QUIET_300),
        ]
        self.assertIsNone(analyse(lines, now=T0 + 320).last_real)
        self.assertEqual(analyse(lines, now=T0 + 400).last_real, T0 + 400)

    def test_code_turn_holds_while_running_and_clears_on_stop(self):
        code = [
            line(0, "[keep-awake] started (id=83, claims=code_turn)"),
            line(0, "[keep-awake] code session hold taken (turn, battery=false)"),
        ]
        done = code + [
            line(218, "[keep-awake] code session hold released (idle, held 218000ms, longest stretch without input 5000ms)"),
            line(218, "[keep-awake] stopped (id=83): last claim code_turn released"),
        ]
        running = analyse(startup() + code)
        self.assertEqual(running.open_holds, ["code_turn", "code session"])
        finished = analyse(startup() + done)
        self.assertEqual(finished.open_holds, [])
        self.assertEqual(finished.last_real, T0 + 218)

    def test_manual_hold_is_open_until_released(self):
        taken = startup() + [line(100, "[keep-awake] session abc123 manual hold taken")]
        self.assertEqual(analyse(taken).open_holds, ["manual hold"])
        released = taken + [line(600, "[keep-awake] session abc123 manual hold released")]
        self.assertEqual(analyse(released).open_holds, [])

    def test_crash_orphan_is_voided_by_a_later_launch(self):
        orphan = [
            line(0, "[keep-awake] started (id=83, claims=code_turn)"),
            line(0, "[keep-awake] code session hold taken (turn, battery=false)"),
        ]
        lines = startup() + orphan + [line(100, "Starting app {")] + rotation(200)
        activity = analyse(lines)
        self.assertEqual(activity.open_holds, [])
        self.assertEqual(activity.last_real, T0)
        self.assertEqual(helper.decide(NOW, True, 0.0, [claude_row()], activity, IDLE),
                         (True, "only Claude rotation claims for 3600s"))

    def test_orphan_after_the_latest_launch_still_blocks(self):
        lines = startup() + [
            line(100, "Starting app {"),
            line(200, "[keep-awake] started (id=84, claims=code_turn)"),
        ]
        self.assertEqual(analyse(lines).open_holds, ["code_turn"])

    def test_launch_line_is_not_activity(self):
        self.assertIsNone(analyse(startup() + [line(100, "Starting app {")]).last_real)

    def test_ongoing_rotation_before_a_launch_is_forgotten(self):
        lines = startup() + rotation(0, release=False) + [line(100, "Starting app {")]
        activity = analyse(lines, now=T0 + 400)
        self.assertIsNone(activity.last_real)
        self.assertEqual(activity.open_holds, [])

    def test_2026_10_19_sequence_ends_with_no_open_holds(self):
        lines = [
            "2026-10-19 07:41:48 [info] [keep-awake] started (id=3, claims=code_turn)",
            "2026-10-19 07:41:48 [info] [keep-awake] code session hold taken (turn, battery=false)",
            "2026-10-19 07:42:32 [info] [remote-tools-device] rotation due — re-presenting credential in-band",
            "2026-10-19 07:42:32 [info] [remote-tools-device] keep-awake held (pss=0)",
            "2026-10-19 07:42:32 [info] [remote-tools-device] served get_device_info",
            "2026-10-19 07:42:32 [info] [remote-tools-device] credential re-verified in-band",
            "2026-10-19 07:42:39 [info] [keep-awake] code session hold released (idle, held 50594ms, longest stretch without input 0ms)",
            "2026-10-19 07:42:47 [info] [keep-awake] code session hold taken (turn, battery=false)",
            "2026-10-19 07:44:15 [info] [keep-awake] code session hold released (idle, held 88087ms, longest stretch without input 0ms)",
            "2026-10-19 07:47:32 [info] [keep-awake] stopped (id=3): last claim remote_tools released",
            "2026-10-19 07:47:32 [info] [remote-tools-device] keep-awake released (quiet, held 300006ms)",
        ]
        now = datetime.datetime(2026, 10, 19, 9, 0, 0).timestamp()
        activity = helper.analyse(lines, now, IDLE, True)
        self.assertEqual(activity.open_holds, [])
        self.assertEqual(activity.last_real, datetime.datetime(2026, 10, 19, 7, 44, 15).timestamp())
        self.assertTrue(activity.history_ok)

    def test_started_code_turn_without_a_stop_still_blocks(self):
        lines = startup() + [line(0, "[keep-awake] started (id=3, claims=code_turn)")]
        self.assertEqual(analyse(lines).open_holds, ["code_turn"])

    def test_id_reuse_leaves_nothing_open_once_the_last_pair_closes(self):
        pairs = [
            line(0, "[keep-awake] started (id=3, claims=code_turn)"),
            line(100, "[keep-awake] stopped (id=3): last claim code_turn released"),
            line(200, "[keep-awake] started (id=3, claims=code_turn)"),
            line(300, "[keep-awake] stopped (id=3): last claim code_turn released"),
        ]
        self.assertEqual(analyse(startup() + pairs).open_holds, [])
        self.assertEqual(analyse(startup() + pairs[:3]).open_holds, ["code_turn"])

    def test_mixed_claims_open_a_blocker(self):
        lines = startup() + [line(0, "[keep-awake] started (id=3, claims=remote_tools,code_turn)")]
        self.assertEqual(analyse(lines).open_holds, ["remote_tools,code_turn"])

    def test_a_stop_clears_open_manual_and_code_session_holds(self):
        lines = startup() + [
            line(0, "[keep-awake] session abc123 manual hold taken"),
            line(0, "[keep-awake] code session hold taken (turn, battery=false)"),
            line(100, "[keep-awake] stopped (id=9): last claim remote_tools released"),
        ]
        self.assertEqual(analyse(lines).open_holds, [])

    def test_rotation_only_blocker_never_opens_a_hold(self):
        lines = startup() + [line(0, "[keep-awake] started (id=3, claims=remote_tools)")] + rotation(600, release=False)
        self.assertEqual(analyse(lines).open_holds, [])

    def test_no_longer_counted_is_activity_and_closes_nothing(self):
        lines = startup() + [
            line(0, "[keep-awake] session abc123 manual hold taken"),
            line(100, "[keep-awake] session abc123 no longer counted"),
        ]
        activity = analyse(lines)
        self.assertEqual(activity.last_real, T0 + 100)
        self.assertEqual(activity.open_holds, ["manual hold"])

    def test_unrecognised_keep_awake_line_is_activity_at_its_time(self):
        activity = analyse(startup() + [line(100, "[keep-awake] something new")])
        self.assertEqual(activity.last_real, T0 + 100)

    def test_keep_awake_line_without_timestamp_is_activity_now(self):
        lines = startup() + ["  [keep-awake] started (id=9, claims=remote_tools)"]
        activity = analyse(lines)
        self.assertEqual(activity.last_real, NOW)
        self.assertEqual(activity.open_holds, [])

    def test_unparseable_line_without_keep_awake_is_skipped(self):
        lines = startup() + ["2026-99-99 99:99:99 [info] nothing to see"]
        self.assertIsNone(analyse(lines).last_real)

    def test_unmatched_stopped_line_is_activity_but_opens_nothing(self):
        activity = analyse(startup() + [line(100, "[keep-awake] stopped (id=5)")])
        self.assertEqual(activity.last_real, T0 + 100)
        self.assertEqual(activity.open_holds, [])

    def test_served_tools_other_than_get_device_info_are_activity(self):
        self.assertIsNone(analyse(startup() + [line(100, "[remote-tools-device] served get_device_info")]).last_real)
        activity = analyse(startup() + [line(100, "[remote-tools-device] served list_sessions")])
        self.assertEqual(activity.last_real, T0 + 100)

    def test_short_history_fails_the_history_check(self):
        activity = analyse([line(4000, "startup message")])
        self.assertFalse(activity.history_ok)

    def test_history_exactly_idle_seconds_back_is_ok(self):
        activity = analyse([line(3600, "startup message")])
        self.assertTrue(activity.history_ok)

    def test_missing_log_fails_the_history_check(self):
        self.assertFalse(analyse([], read_ok=False).history_ok)

    def test_empty_log_fails_the_history_check(self):
        self.assertFalse(analyse([]).history_ok)

    def test_log_without_timestamps_fails_the_history_check(self):
        self.assertFalse(analyse(["no timestamp here"]).history_ok)


class ReadClaudeLogsTest(TempDirTest):
    def test_sibling_is_named_with_a_one_before_the_extension(self):
        self.assertEqual(helper.sibling_log_path("/x/main.log"), "/x/main1.log")

    def test_missing_sibling_is_fine(self):
        main = write_log(self.dir, "main.log", startup())
        lines, files, ok = helper.read_claude_logs(main)
        self.assertTrue(ok)
        self.assertEqual(lines, startup())
        self.assertIsNone(files[0][1])
        self.assertEqual(files[1][1], os.path.getsize(main))

    def test_rotated_sibling_is_read_before_the_main_log(self):
        write_log(self.dir, "main1.log", [line(0, "older")])
        main = write_log(self.dir, "main.log", [line(1, "newer")])
        lines, _files, ok = helper.read_claude_logs(main)
        self.assertTrue(ok)
        self.assertEqual(lines, [line(0, "older"), line(1, "newer")])

    def test_hold_opened_in_rotated_file_is_still_open_in_main(self):
        write_log(self.dir, "main1.log", [line(0, "startup message"),
                                          line(0, "[keep-awake] started (id=83, claims=code_turn)")])
        main = write_log(self.dir, "main.log", rotation(1800, release=False))
        lines, _files, ok = helper.read_claude_logs(main)
        activity = analyse(lines, read_ok=ok)
        self.assertTrue(activity.history_ok)
        self.assertEqual(activity.open_holds, ["code_turn"])

    def test_hold_opened_in_rotated_file_is_closed_by_a_stop_in_main(self):
        write_log(self.dir, "main1.log", startup() + [line(0, "[keep-awake] started (id=83, claims=code_turn)")])
        main = write_log(self.dir, "main.log", [line(600, "[keep-awake] stopped (id=83): last claim code_turn released")])
        lines, _files, ok = helper.read_claude_logs(main)
        self.assertEqual(analyse(lines, read_ok=ok).open_holds, [])

    def test_launch_in_main1_voids_a_hold_opened_earlier_in_main1(self):
        write_log(self.dir, "main1.log", [
            line(0, "startup message"),
            line(0, "[keep-awake] started (id=83, claims=code_turn)"),
            line(100, "Starting app {"),
        ])
        main = write_log(self.dir, "main.log", rotation(1800, release=False))
        lines, _files, ok = helper.read_claude_logs(main)
        self.assertEqual(analyse(lines, read_ok=ok).open_holds, [])

    def test_hold_opened_after_a_launch_in_main1_still_blocks_when_main_follows(self):
        write_log(self.dir, "main1.log", [
            line(0, "startup message"),
            line(100, "Starting app {"),
            line(200, "[keep-awake] started (id=83, claims=code_turn)"),
        ])
        main = write_log(self.dir, "main.log", rotation(1800, release=False))
        lines, _files, ok = helper.read_claude_logs(main)
        self.assertEqual(analyse(lines, read_ok=ok).open_holds, ["code_turn"])

    def test_missing_main_log_fails(self):
        write_log(self.dir, "main1.log", startup())
        lines, _files, ok = helper.read_claude_logs(os.path.join(self.dir, "main.log"))
        self.assertFalse(ok)
        self.assertEqual(lines, [])

    def test_over_the_cap_fails_without_reading(self):
        main = write_log(self.dir, "main.log", startup())
        lines, _files, ok = helper.read_claude_logs(main, cap=10)
        self.assertFalse(ok)
        self.assertEqual(lines, [])

    def test_unreadable_main_log_fails(self):
        main = os.path.join(self.dir, "main.log")
        os.mkdir(main)
        lines, _files, ok = helper.read_claude_logs(main)
        self.assertFalse(ok)
        self.assertEqual(lines, [])

    def test_rotation_during_read_is_retried_once(self):
        write_log(self.dir, "main1.log", [line(0, "older")])
        main = write_log(self.dir, "main.log", [line(1, "newer")])
        with mock.patch.object(helper, "identities", side_effect=[(1, 2), (3, 2), (3, 2), (3, 2)]):
            lines, _files, ok = helper.read_claude_logs(main)
        self.assertTrue(ok)
        self.assertEqual(lines, [line(0, "older"), line(1, "newer")])

    def test_rotation_that_never_settles_fails(self):
        write_log(self.dir, "main1.log", [line(0, "older")])
        main = write_log(self.dir, "main.log", [line(1, "newer")])
        steps = [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1)]
        with mock.patch.object(helper, "identities", side_effect=steps):
            _lines, _files, ok = helper.read_claude_logs(main)
        self.assertFalse(ok)


class LineSplitterTest(unittest.TestCase):
    def test_partial_line_waits_for_the_rest(self):
        splitter = helper.LineSplitter()
        self.assertEqual(splitter.feed(b"IDLE\nRES"), ["IDLE"])
        self.assertEqual(splitter.feed(b"UME\n"), ["RESUME"])

    def test_every_complete_line_in_one_chunk_is_returned(self):
        self.assertEqual(helper.LineSplitter().feed(b"IDLE\nRESUME\n"), ["IDLE", "RESUME"])

    def test_lines_are_stripped_and_unknown_lines_come_through(self):
        self.assertEqual(helper.LineSplitter().feed(b"garbage\n IDLE \n"), ["garbage", "IDLE"])

    def test_pipe_reader_returns_bytes_as_they_arrive_in_an_in_process_pipe(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)

        class PipeEnd:
            def fileno(self):
                return read_fd

        reader = helper.pipe_reader(PipeEnd())
        splitter = helper.LineSplitter()
        os.write(write_fd, b"IDLE\nRES")
        self.assertEqual(splitter.feed(reader()), ["IDLE"])
        os.write(write_fd, b"UME\n")
        self.assertEqual(splitter.feed(reader()), ["RESUME"])


class ResumeGraceTest(unittest.TestCase):
    def test_first_tick_starts_no_grace(self):
        self.assertEqual(helper.update_grace(None, 0.0, NOW, 0.0), 0.0)

    def test_five_seconds_of_growth_is_not_a_resume(self):
        self.assertEqual(helper.update_grace(0.0, 5.0, NOW, 0.0), 0.0)

    def test_more_than_five_seconds_of_growth_starts_a_600_second_grace(self):
        self.assertEqual(helper.update_grace(0.0, 5.5, NOW, 0.0), NOW + 600)

    def test_shrinking_delta_keeps_the_grace_already_running(self):
        self.assertEqual(helper.update_grace(10.0, 2.0, NOW, NOW + 50), NOW + 50)


class InhibitionTest(unittest.TestCase):
    def test_claude_only_rows_parse(self):
        rows = helper.parse_inhibitions(inhibit_json([["sleep", CLAUDE_APP, "Electron", "block", 3]]))
        self.assertEqual(rows, [helper.Inhibition("sleep", CLAUDE_APP, "Electron", "block", 3)])

    def test_mixed_rows_keep_every_app(self):
        rows = helper.parse_inhibitions(inhibit_json([
            ["idle", "Steam", "Game streaming (server)", "block", 3],
            ["sleep", CLAUDE_APP, "Electron", "block", 3],
        ]))
        self.assertEqual([row.app for row in rows], ["Steam", CLAUDE_APP])

    def test_empty_list_parses_to_empty(self):
        self.assertEqual(helper.parse_inhibitions(inhibit_json([])), [])

    def test_malformed_json_is_unknown(self):
        self.assertIsNone(helper.parse_inhibitions("{not json"))
        self.assertIsNone(helper.parse_inhibitions(None))

    def test_wrong_type_is_unknown(self):
        self.assertIsNone(helper.parse_inhibitions(json.dumps({"type": "s", "data": "x"})))

    def test_bad_row_shapes_are_unknown(self):
        bad_rows = [
            [["sleep", CLAUDE_APP, "Electron", "block"]],
            [["sleep", CLAUDE_APP, "Electron", "block", True]],
            [[1, CLAUDE_APP, "Electron", "block", 3]],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                self.assertIsNone(helper.parse_inhibitions(inhibit_json(rows)))

    def test_read_runs_the_exact_busctl_command_with_a_timeout(self):
        runner = FakeRunner()
        rows = helper.read_inhibitions(runner)
        self.assertEqual(rows, [claude_row()])
        cmd, kwargs = runner.calls[0]
        self.assertEqual(cmd, helper.INHIBIT_CMD)
        self.assertEqual(kwargs, {"capture_output": True, "text": True, "timeout": 5})

    def test_busctl_failure_is_unknown(self):
        self.assertIsNone(helper.read_inhibitions(FakeRunner(inhibit_returncode=1)))

    def test_runner_errors_are_unknown(self):
        for error in (OSError("no busctl"), subprocess.TimeoutExpired("busctl", 5)):
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(helper.read_inhibitions(FakeRunner(inhibit_error=error)))


class DecideTest(unittest.TestCase):
    def decide(self, **changes):
        args = dict(now=NOW, idle=True, grace_until=0.0, inhibitions=[claude_row()],
                    activity=ACTIVE, idle_seconds=IDLE)
        args.update(changes)
        return helper.decide(**args)

    def test_not_idle_comes_first(self):
        self.assertEqual(self.decide(idle=False, grace_until=NOW + 600, inhibitions=None), (False, "not idle"))

    def test_grace_comes_before_the_inhibition_checks(self):
        self.assertEqual(self.decide(grace_until=NOW + 600, inhibitions=None),
                         (False, "grace period after resume"))

    def test_grace_is_over_when_now_reaches_it(self):
        self.assertEqual(self.decide(grace_until=NOW), (True, "only Claude rotation claims for 3600s"))

    def test_unknown_inhibitions(self):
        self.assertEqual(self.decide(inhibitions=None), (False, "inhibitions unreadable"))

    def test_empty_inhibitions(self):
        self.assertEqual(self.decide(inhibitions=[]), (False, "no inhibitions (PowerDevil handles sleep)"))

    def test_other_app_blocks_before_the_log_checks(self):
        activity = helper.Activity(NOW, ["code_turn"], False)
        self.assertEqual(self.decide(inhibitions=[claude_row(), other_row()], activity=activity),
                         (False, "blocked by Steam"))

    def test_first_non_claude_app_is_named(self):
        rows = [claude_row(), other_row("Steam"), other_row("mpv")]
        self.assertEqual(self.decide(inhibitions=rows), (False, "blocked by Steam"))

    def test_history_too_short(self):
        self.assertEqual(self.decide(activity=helper.Activity(None, [], False)),
                         (False, "Claude log history too short"))

    def test_missing_activity_counts_as_history_too_short(self):
        self.assertEqual(self.decide(activity=None), (False, "Claude log history too short"))

    def test_open_holds_are_named(self):
        activity = helper.Activity(None, ["code_turn", "manual hold"], True)
        self.assertEqual(self.decide(activity=activity),
                         (False, "Claude hold in progress: code_turn, manual hold"))

    def test_recent_activity(self):
        activity = helper.Activity(NOW - 120, [], True)
        self.assertEqual(self.decide(activity=activity), (False, "Claude activity 120s ago"))

    def test_activity_exactly_idle_seconds_ago_allows_suspend(self):
        activity = helper.Activity(NOW - IDLE, [], True)
        self.assertEqual(self.decide(activity=activity), (True, "only Claude rotation claims for 3600s"))

    def test_no_activity_suspends(self):
        self.assertEqual(self.decide(activity=helper.Activity(None, [], True)),
                         (True, "only Claude rotation claims for 3600s"))

    def test_history_is_checked_before_holds(self):
        activity = helper.Activity(NOW - 10, ["code_turn"], False)
        self.assertEqual(self.decide(activity=activity), (False, "Claude log history too short"))

    def test_grace_is_checked_before_the_cooldown(self):
        self.assertEqual(self.decide(grace_until=NOW + 600, cooldown_until=NOW + 180),
                         (False, "grace period after resume"))

    def test_cooldown_is_checked_before_the_inhibition_checks(self):
        self.assertEqual(self.decide(inhibitions=None, cooldown_until=NOW + 180),
                         (False, "suspend already requested"))
        self.assertEqual(self.decide(inhibitions=[other_row()], cooldown_until=NOW + 180),
                         (False, "suspend already requested"))

    def test_not_idle_is_checked_before_the_cooldown(self):
        self.assertEqual(self.decide(idle=False, cooldown_until=NOW + 180), (False, "not idle"))

    def test_cooldown_ends_exactly_when_now_reaches_it(self):
        self.assertEqual(self.decide(cooldown_until=NOW), (True, "only Claude rotation claims for 3600s"))

    def test_holds_are_checked_before_recent_activity(self):
        activity = helper.Activity(NOW - 10, ["code_turn"], True)
        self.assertEqual(self.decide(activity=activity), (False, "Claude hold in progress: code_turn"))


class MonitorTest(TempDirTest):
    def monitor(self, log_path, runner, out, clock=None, dry_run=False):
        clock = clock or FakeTime()
        return helper.Monitor(IDLE, log_path, make_deps(runner, out, clock), dry_run), clock

    def test_real_decision_calls_suspend_once_with_expected_arguments(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(), []
        monitor, _clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        self.assertEqual(runner.suspend_calls(), [(helper.SUSPEND_CMD,
                                                   {"capture_output": True, "text": True, "timeout": 30})])
        self.assertIn("suspend: only Claude rotation claims for 3600s", out)

    def test_dry_run_never_calls_suspend(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(), []
        monitor, _clock = self.monitor(log, runner, out.append, dry_run=True)
        monitor.on_line("IDLE")
        monitor.evaluate()
        self.assertEqual(runner.suspend_calls(), [])
        self.assertIn("would suspend: only Claude rotation claims for 3600s", out)

    def test_suspend_failure_is_logged_and_the_loop_keeps_going(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(suspend_returncode=1), []
        monitor, clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        clock.now += helper.SUSPEND_COOLDOWN_SECONDS + 1
        monitor.evaluate()
        self.assertEqual(len(runner.suspend_calls()), 2)
        self.assertTrue(any(msg.startswith("suspend failed") for msg in out))

    def test_suspend_timeout_is_logged_not_raised(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(suspend_error=subprocess.TimeoutExpired("busctl", 30)), []
        monitor, _clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        self.assertTrue(any(msg.startswith("suspend failed") for msg in out))

    def test_recent_activity_blocks_and_resume_cancels(self):
        log = write_log(self.dir, "main.log", startup() + [line(7140, "[remote-tools-device] served list_sessions")])
        runner, out = FakeRunner(), []
        monitor, _clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        monitor.on_line("RESUME")
        monitor.evaluate()
        self.assertEqual(runner.suspend_calls(), [])
        self.assertIn("Claude activity 60s ago", out)
        self.assertIn("not idle", out)

    def test_resume_jump_holds_off_until_the_grace_expires(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(), []
        monitor, clock = self.monitor(log, runner, out.append)
        monitor.evaluate()
        clock.boot_offset += 100
        monitor.on_line("IDLE")
        monitor.evaluate()
        self.assertEqual(runner.suspend_calls(), [])
        self.assertIn("grace period after resume", out)
        clock.now += helper.GRACE_SECONDS + 1
        monitor.evaluate()
        self.assertEqual(len(runner.suspend_calls()), 1)

    def test_real_suspend_sets_a_cooldown_that_blocks_the_next_poll(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(), []
        monitor, clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        self.assertEqual(len(runner.suspend_calls()), 1)
        self.assertEqual(monitor.cooldown_until, NOW + helper.SUSPEND_COOLDOWN_SECONDS)
        self.assertEqual(monitor.grace_until, 0.0)
        clock.now += 60
        with mock.patch.object(helper, "read_activity") as read:
            monitor.evaluate()
        self.assertEqual(len(runner.suspend_calls()), 1)
        self.assertEqual(read.call_count, 0)
        self.assertIn("suspend already requested", out)

    def test_suspend_can_happen_again_once_the_cooldown_has_passed(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(), []
        monitor, clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        clock.now = monitor.cooldown_until
        monitor.evaluate()
        self.assertEqual(len(runner.suspend_calls()), 2)

    def test_failed_suspend_call_still_sets_the_cooldown(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(suspend_returncode=1), []
        monitor, clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        self.assertTrue(any(msg.startswith("suspend failed") for msg in out))
        self.assertEqual(monitor.cooldown_until, NOW + helper.SUSPEND_COOLDOWN_SECONDS)
        clock.now += 60
        monitor.evaluate()
        self.assertEqual(len(runner.suspend_calls()), 1)
        self.assertIn("suspend already requested", out)

    def test_dry_run_never_sets_the_cooldown(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(), []
        monitor, clock = self.monitor(log, runner, out.append, dry_run=True)
        monitor.on_line("IDLE")
        monitor.evaluate()
        clock.now += 60
        monitor.evaluate()
        self.assertEqual(monitor.cooldown_until, 0.0)
        self.assertEqual(runner.suspend_calls(), [])
        self.assertEqual(out.count("would suspend: only Claude rotation claims for 3600s"), 2)
        self.assertFalse(any("suspend already requested" in msg for msg in out))

    def test_claude_log_is_not_read_when_another_app_blocks(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner = FakeRunner(inhibit_stdout=inhibit_json([["idle", "Steam", "Game", "block", 3]]))
        out = []
        monitor, _clock = self.monitor(log, runner, out.append)
        monitor.on_line("IDLE")
        monitor.evaluate()
        self.assertEqual(out, ["blocked by Steam"])


class ServeTest(TempDirTest):
    def test_idle_with_rotation_only_suspends_once_then_eof_returns_1(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        runner, out = FakeRunner(), []
        deps = make_deps(runner, out.append, FakeTime())
        monitor = helper.Monitor(IDLE, log, deps)
        result = helper.serve(monitor, FakeStream([b"IDLE\n"]).read_chunk, FakeSelector([True]), 60)
        self.assertEqual(result, 1)
        self.assertEqual(len(runner.suspend_calls()), 1)

    def test_idle_then_resume_with_recent_activity_never_suspends(self):
        log = write_log(self.dir, "main.log", startup() + [line(7140, "[remote-tools-device] served list_sessions")])
        runner, out = FakeRunner(), []
        deps = make_deps(runner, out.append, FakeTime())
        monitor = helper.Monitor(IDLE, log, deps)
        result = helper.serve(monitor, FakeStream([b"IDLE\n", b"RESUME\n"]).read_chunk,
                              FakeSelector([True, False, True, False]), 60)
        self.assertEqual(result, 1)
        self.assertEqual(runner.suspend_calls(), [])
        self.assertIn("Claude activity 60s ago", out)
        self.assertIn("not idle", out)

    def make_monitor(self, lines):
        log = write_log(self.dir, "main.log", lines)
        runner, out = FakeRunner(), []
        return helper.Monitor(IDLE, log, make_deps(runner, out.append, FakeTime())), runner, out

    def test_idle_and_resume_in_one_chunk_end_not_idle(self):
        monitor, runner, out = self.make_monitor(rotation_only_log())
        result = helper.serve(monitor, FakeStream([b"IDLE\nRESUME\n"]).read_chunk, FakeSelector([True, False]), 60)
        self.assertEqual(result, 1)
        self.assertFalse(monitor.idle)
        self.assertEqual(runner.suspend_calls(), [])
        self.assertEqual(out, ["not idle"])

    def test_idle_split_across_two_chunks_is_joined_before_it_is_applied(self):
        monitor, runner, out = self.make_monitor(startup() + [line(7140, "[remote-tools-device] served list_sessions")])
        result = helper.serve(monitor, FakeStream([b"IDLE\nRES", b"UME\n"]).read_chunk,
                              FakeSelector([True, True, False]), 60)
        self.assertEqual(result, 1)
        self.assertFalse(monitor.idle)
        self.assertEqual(runner.suspend_calls(), [])
        self.assertIn("Claude activity 60s ago", out)
        self.assertIn("not idle", out)

    def test_unknown_lines_are_ignored(self):
        monitor, runner, out = self.make_monitor(rotation_only_log())
        helper.serve(monitor, FakeStream([b"garbage\nmore noise\n"]).read_chunk, FakeSelector([True]), 60)
        self.assertFalse(monitor.idle)
        self.assertEqual(out, ["not idle"])

    def test_end_of_output_returns_1(self):
        monitor, _runner, _out = self.make_monitor(rotation_only_log())
        self.assertEqual(helper.serve(monitor, FakeStream([]).read_chunk, FakeSelector([True]), 60), 1)


class MainTest(TempDirTest):
    def test_missing_swayidle_exits_2_without_spawning(self):
        out, spawn = [], FakeSpawn(FakeStream([]))
        deps = make_deps(FakeRunner(), out.append, FakeTime(), spawn=spawn, which=lambda name: None)
        self.assertEqual(helper.main(["--log", os.path.join(self.dir, "main.log")], deps), 2)
        self.assertEqual(spawn.calls, [])
        self.assertIn("sudo pacman -S --needed swayidle", out[0])

    def test_swayidle_command_line_and_eof_returns_1(self):
        out, spawn = [], FakeSpawn(FakeStream([]))
        selector = FakeSelector([])
        deps = make_deps(FakeRunner(), out.append, FakeTime(), spawn=spawn, selector=selector)
        result = helper.main(["--log", os.path.join(self.dir, "main.log")], deps)
        self.assertEqual(result, 1)
        self.assertEqual(spawn.calls, [(["swayidle", "timeout", "3600", "echo IDLE", "resume", "echo RESUME"],
                                        {"stdout": subprocess.PIPE, "bufsize": 0})])
        self.assertTrue(spawn.proc.terminated)

    def test_dry_run_runs_until_interrupted_and_never_suspends(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        out, runner = [], FakeRunner()
        spawn = FakeSpawn(FakeStream([b"IDLE\n"]))
        selector = FakeSelector([False, False, True, False, "interrupt"])
        deps = make_deps(runner, out.append, FakeTime(), spawn=spawn, selector=selector)
        result = helper.main(["--dry-run", "--log", log], deps)
        self.assertEqual(result, 0)
        self.assertEqual(selector.timeouts, [60, 60, 60, 60, 60])
        self.assertEqual(runner.suspend_calls(), [])
        self.assertIn("would suspend: only Claude rotation claims for 3600s", out)
        self.assertTrue(spawn.proc.terminated)

    def test_explain_reads_logs_and_never_spawns_or_suspends(self):
        log = write_log(self.dir, "main.log", rotation_only_log())
        out, runner = [], FakeRunner()
        spawn = FakeSpawn(FakeStream([]))
        deps = make_deps(runner, out.append, FakeTime(), spawn=spawn)
        self.assertEqual(helper.main(["--explain", "--log", log], deps), 0)
        self.assertEqual(spawn.calls, [])
        self.assertEqual(runner.suspend_calls(), [])
        self.assertTrue(all(cmd == helper.INHIBIT_CMD for cmd, _ in runner.calls))
        self.assertIn(f"  {log}: {os.path.getsize(log)} bytes", out)
        self.assertIn(f"  {os.path.join(self.dir, 'main1.log')}: missing", out)
        self.assertIn("history ok: yes", out)
        self.assertIn("decision: would suspend: only Claude rotation claims for 3600s", out)

    def test_defaults_and_validation(self):
        parser = helper.build_parser()
        args = parser.parse_args([])
        self.assertEqual((args.idle_seconds, args.poll_seconds, args.log, args.dry_run, args.explain),
                         (3600, 60, None, False, False))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(["--idle-seconds", "0"])
        self.assertEqual(ctx.exception.code, 2)

    def test_help_exits_zero(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                helper.build_parser().parse_args(["-h"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
