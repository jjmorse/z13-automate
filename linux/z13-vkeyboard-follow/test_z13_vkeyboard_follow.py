"""Hermetic unit tests for z13-vkeyboard-follow.py: fake keyboards, fake buses, a fake /proc tree and fake schedulers only, and nothing here touches the real bus or signals a process."""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import signal
import tempfile
import unittest
from unittest import mock

from gi.repository import Gio, GLib, GLibUnix

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "z13-vkeyboard-follow.py")
ATTACHED = "Z13_VKEYBOARD_ATTACHED_MODE"
DETACHED = "Z13_VKEYBOARD_DETACHED_MODE"
RESTART = "Z13_VKEYBOARD_RESTART"
PROCESS = "Z13_VKEYBOARD_PROCESS"
KWIN_PID = 4321
UID = 1000
NAME = "plasma-keyboard"
LOGIND = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
LOGIND_IFACE = "org.freedesktop.login1.Manager"
DID_NOT_RETURN = "z13-vkeyboard-follow: on-screen keyboard did not come back within 3 s (killed pid {pid}); KWin may have given up after repeated crashes\n"


def load_module(name):
    loader = importlib.machinery.SourceFileLoader(name, PATH)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


follow = load_module("z13_vkeyboard_follow")


class FakeKeyboard:
    """Stands in for KWinVirtualKeyboard: records every write and can fail on get or set."""

    def __init__(self, mode, get_error=None, set_error=None):
        self.mode = mode
        self.get_error = get_error
        self.set_error = set_error
        self.set_calls = []

    def get_mode(self):
        if self.get_error is not None:
            raise self.get_error
        return self.mode

    def set_mode(self, mode):
        self.set_calls.append(mode)
        if self.set_error is not None:
            raise self.set_error
        self.mode = mode


class FakeScheduler:
    """Collects scheduled callbacks instead of using a GLib main loop; fire_next() runs the oldest one."""

    def __init__(self):
        self.calls = []

    def __call__(self, delay_ms, callback):
        self.calls.append((delay_ms, callback))

    def fire_next(self):
        _delay_ms, callback = self.calls.pop(0)
        callback()


class FakeBus:
    """Answers the tabletMode read with a fixed value or a fixed error; a real bus is never touched."""

    def __init__(self, tablet=True, error=None):
        self.tablet = tablet
        self.error = error
        self.reads = 0

    def call_sync(self, dest, path, iface, method, params, reply_type, flags, timeout, cancellable):
        self.reads += 1
        if self.error is not None:
            raise self.error
        return GLib.Variant("(v)", (GLib.Variant("b", self.tablet),))


class FakeClock:
    """Returns a clock reading that the test sets by hand."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeKill:
    """Records each signal and raises the error scripted for a pid, if any; no real process is signalled."""

    def __init__(self, errors=None):
        self.errors = errors or {}
        self.calls = []

    def __call__(self, pid, sig):
        self.calls.append((pid, sig))
        if pid in self.errors:
            raise self.errors[pid]


class FakeFind:
    """Returns the scripted pid lists in order, then keeps returning the last one; records each query."""

    def __init__(self, *results):
        self.results = list(results)
        self.queries = []

    def __call__(self, kwin_pid, name, uid):
        self.queries.append((kwin_pid, name, uid))
        if len(self.results) > 1:
            return list(self.results.pop(0))
        return list(self.results[0])


class FakeRestarter:
    """Records each restart reason in place of a Restarter, so no process is signalled."""

    def __init__(self):
        self.reasons = []

    def restart(self, reason):
        self.reasons.append(reason)


class FakeDaemon:
    """Answers GetConnectionUnixProcessID with a fixed pid or a fixed error; a real bus is never touched."""

    def __init__(self, pid=KWIN_PID, error=None):
        self.pid = pid
        self.error = error
        self.calls = []

    def call_sync(self, dest, path, iface, method, params, reply_type, flags, timeout, cancellable):
        self.calls.append((dest, path, iface, method, params.unpack()))
        if self.error is not None:
            raise self.error
        return GLib.Variant("(u)", (self.pid,))


class FakeSignalBus:
    """Records signal subscriptions on a fake system bus, so no real bus is touched."""

    def __init__(self):
        self.subscriptions = []

    def signal_subscribe(self, sender, iface, member, path, arg0, flags, callback):
        self.subscriptions.append((sender, iface, member, path, arg0, callback))


def run_apply(keyboard, tablet_on, attached=0, detached=1):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = follow.apply_mode(keyboard, tablet_on, attached, detached)
    return ok, out.getvalue()


def scripted_attempt(outcomes, default):
    """Return (attempt, attempts): attempt() returns the scripted outcomes in order, then default; attempts records each call."""
    attempts = []
    remaining = list(outcomes)

    def attempt():
        attempts.append(True)
        return remaining.pop(0) if remaining else default

    return attempt, attempts


def run_logged(action):
    """Run action with stdout captured, and return what it logged."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        action()
    return out.getvalue()


def write_proc(root, entry, comm=NAME, ppid=KWIN_PID, uid=UID):
    """Write a fake /proc/<entry> holding comm, stat and status, laid out the way the kernel lays them out."""
    directory = os.path.join(root, entry)
    os.makedirs(directory, exist_ok=True)
    files = {
        "comm": f"{comm}\n",
        "stat": f"{entry} ({comm}) S {ppid} {entry} {entry} 0 -1\n",
        "status": f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n",
    }
    for leaf, text in files.items():
        with open(os.path.join(directory, leaf), "w") as f:
            f.write(text)


def fake_restarter(find, kill, clock, scheduler, kwin=KWIN_PID, **options):
    """Build a Restarter from fakes only, so no real process is signalled and no real timer runs."""
    return follow.Restarter(lambda: kwin, NAME, UID, find=find, kill=kill, clock=clock, schedule=scheduler, **options)


class ModeTest(unittest.TestCase):
    def test_defaults_are_0_attached_and_1_detached(self):
        attached, detached = follow.load_modes({})
        self.assertEqual((attached, detached), (0, 1))
        self.assertEqual(follow.desired_mode(False, attached, detached), 0)
        self.assertEqual(follow.desired_mode(True, attached, detached), 1)

    def test_overridden_env_changes_both_modes(self):
        attached, detached = follow.load_modes({ATTACHED: "2", DETACHED: "0"})
        self.assertEqual(follow.desired_mode(False, attached, detached), 2)
        self.assertEqual(follow.desired_mode(True, attached, detached), 0)

    def test_one_override_keeps_the_other_default(self):
        self.assertEqual(follow.load_modes({DETACHED: "2"}), (0, 2))
        self.assertEqual(follow.load_modes({ATTACHED: "1"}), (1, 1))

    def test_non_integer_env_is_rejected_and_names_the_variable(self):
        for bad in ("", "on", "1.5", "0x1", "two"):
            for name in (ATTACHED, DETACHED):
                with self.subTest(name=name, value=bad):
                    with self.assertRaises(ValueError) as ctx:
                        follow.load_modes({name: bad})
                    self.assertIn(name, str(ctx.exception))


class ApplyModeTest(unittest.TestCase):
    def test_tablet_mode_on_writes_detached_mode(self):
        keyboard = FakeKeyboard(mode=0)
        ok, out = run_apply(keyboard, True)
        self.assertTrue(ok)
        self.assertEqual(keyboard.set_calls, [1])
        self.assertEqual(out, "tablet mode on: virtual keyboard mode set to 1\n")

    def test_tablet_mode_off_writes_attached_mode(self):
        keyboard = FakeKeyboard(mode=1)
        ok, out = run_apply(keyboard, False)
        self.assertTrue(ok)
        self.assertEqual(keyboard.set_calls, [0])
        self.assertEqual(out, "tablet mode off: virtual keyboard mode set to 0\n")

    def test_no_write_when_mode_already_matches(self):
        keyboard = FakeKeyboard(mode=1)
        ok, out = run_apply(keyboard, True)
        self.assertTrue(ok)
        self.assertEqual(keyboard.set_calls, [])
        self.assertEqual(out, "tablet mode on: virtual keyboard mode already 1\n")

    def test_repeated_apply_with_same_state_writes_once(self):
        keyboard = FakeKeyboard(mode=0)
        run_apply(keyboard, True)
        run_apply(keyboard, True)
        self.assertEqual(keyboard.set_calls, [1])

    def test_overridden_modes_are_the_ones_written(self):
        keyboard = FakeKeyboard(mode=1)
        run_apply(keyboard, False, attached=2, detached=0)
        self.assertEqual(keyboard.set_calls, [2])

    def test_failing_get_is_logged_once_and_does_not_raise(self):
        keyboard = FakeKeyboard(mode=0, get_error=GLib.Error("no such object"))
        ok, out = run_apply(keyboard, True)
        self.assertFalse(ok)
        self.assertEqual(keyboard.set_calls, [])
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("no such object", lines[0])

    def test_failing_set_is_logged_once_and_does_not_raise(self):
        keyboard = FakeKeyboard(mode=0, set_error=GLib.Error("access denied"))
        ok, out = run_apply(keyboard, True)
        self.assertFalse(ok)
        self.assertEqual(keyboard.set_calls, [1])
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("access denied", lines[0])

    def test_later_apply_retries_after_a_failed_set(self):
        keyboard = FakeKeyboard(mode=0, set_error=GLib.Error("access denied"))
        run_apply(keyboard, True)
        keyboard.set_error = None
        ok, _out = run_apply(keyboard, True)
        self.assertTrue(ok)
        self.assertEqual(keyboard.mode, 1)


class RetrierTest(unittest.TestCase):
    def test_failed_attempt_schedules_one_retry_two_seconds_later(self):
        attempt, attempts = scripted_attempt([False], default=True)
        scheduler = FakeScheduler()
        with contextlib.redirect_stdout(io.StringIO()):
            follow.Retrier(attempt, scheduler).run()
        self.assertEqual(len(attempts), 1)
        self.assertEqual([delay for delay, _ in scheduler.calls], [2000])

    def test_successful_attempt_schedules_nothing(self):
        attempt, attempts = scripted_attempt([True], default=False)
        scheduler = FakeScheduler()
        follow.Retrier(attempt, scheduler).run()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(scheduler.calls, [])

    def test_gives_up_after_three_retries_in_a_row(self):
        attempt, attempts = scripted_attempt([], default=False)
        scheduler = FakeScheduler()
        retrier = follow.Retrier(attempt, scheduler)
        retrier.run()
        while scheduler.calls:
            scheduler.fire_next()
        self.assertEqual(len(attempts), 4)  # the first attempt plus three retries
        self.assertEqual(scheduler.calls, [])
        self.assertEqual(retrier.failures, 3)

    def test_success_on_a_retry_clears_the_counter(self):
        attempt, attempts = scripted_attempt([False, False, True], default=False)
        scheduler = FakeScheduler()
        retrier = follow.Retrier(attempt, scheduler)
        retrier.run()
        scheduler.fire_next()
        scheduler.fire_next()
        self.assertEqual(len(attempts), 3)
        self.assertEqual(retrier.failures, 0)
        self.assertEqual(scheduler.calls, [])

    def test_a_new_event_starts_a_fresh_streak_of_three(self):
        attempt, attempts = scripted_attempt([], default=False)
        scheduler = FakeScheduler()
        retrier = follow.Retrier(attempt, scheduler)
        retrier.run()
        while scheduler.calls:
            scheduler.fire_next()
        retrier.run()
        while scheduler.calls:
            scheduler.fire_next()
        self.assertEqual(len(attempts), 8)

    def test_a_new_event_makes_a_pending_retry_stale(self):
        attempt, attempts = scripted_attempt([False, False], default=True)
        scheduler = FakeScheduler()
        retrier = follow.Retrier(attempt, scheduler)
        retrier.run()  # fails, so retry A is pending
        retrier.run()  # a new event fails too, so retry B is pending
        self.assertEqual(len(scheduler.calls), 2)
        scheduler.fire_next()  # retry A is stale and must do nothing
        self.assertEqual(len(attempts), 2)
        scheduler.fire_next()  # retry B runs and succeeds
        self.assertEqual(len(attempts), 3)

    def test_cancel_drops_a_pending_retry(self):
        attempt, attempts = scripted_attempt([False], default=True)
        scheduler = FakeScheduler()
        retrier = follow.Retrier(attempt, scheduler)
        retrier.run()
        retrier.cancel()
        scheduler.fire_next()
        self.assertEqual(len(attempts), 1)


class FollowerTest(unittest.TestCase):
    def test_appearing_applies_now_and_again_after_1500_ms(self):
        bus = FakeBus(tablet=True)
        keyboard = FakeKeyboard(mode=0)
        scheduler = FakeScheduler()
        follower = follow.Follower(bus, keyboard, 0, 1, schedule=scheduler)
        with contextlib.redirect_stdout(io.StringIO()):
            follower.on_kwin_appeared(None, "org.kde.KWin", ":1.7")
            self.assertEqual(keyboard.set_calls, [1])
            self.assertEqual([delay for delay, _ in scheduler.calls], [1500])
            scheduler.fire_next()
        self.assertEqual(bus.reads, 2)
        self.assertEqual(keyboard.set_calls, [1])  # already 1 on the second pass, so no second write

    def test_vanishing_drops_pending_retries_and_the_settle(self):
        bus = FakeBus(error=GLib.Error("name not provided"))
        keyboard = FakeKeyboard(mode=0)
        scheduler = FakeScheduler()
        follower = follow.Follower(bus, keyboard, 0, 1, schedule=scheduler)
        with contextlib.redirect_stdout(io.StringIO()):
            follower.on_kwin_appeared(None, "org.kde.KWin", ":1.7")
            follower.on_kwin_vanished(None, "org.kde.KWin", "")
            reads_before = bus.reads
            while scheduler.calls:
                scheduler.fire_next()
        self.assertEqual(bus.reads, reads_before)


class FindKeyboardPidsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def find(self, kwin_pid=KWIN_PID, name=NAME, uid=UID):
        return follow.find_keyboard_pids(kwin_pid, name, uid, proc_root=self.root)

    def test_matches_name_parent_and_uid(self):
        write_proc(self.root, "300")
        self.assertEqual(self.find(), [300])

    def test_ignores_wrong_name(self):
        write_proc(self.root, "300", comm="firefox")
        self.assertEqual(self.find(), [])

    def test_ignores_wrong_parent(self):
        write_proc(self.root, "300", ppid=KWIN_PID + 1)
        self.assertEqual(self.find(), [])

    def test_ignores_other_uid(self):
        write_proc(self.root, "300", uid=UID + 1)
        self.assertEqual(self.find(), [])

    def test_ignores_non_numeric_directories(self):
        for entry in ("self", "cpuinfo", "1a", "-5"):
            with self.subTest(entry=entry):
                write_proc(self.root, entry)
        self.assertEqual(self.find(), [])

    def test_parses_stat_after_the_last_parenthesis_when_the_name_has_spaces_and_a_parenthesis(self):
        odd = "kbd helper)"
        write_proc(self.root, "700", comm=odd, ppid=KWIN_PID)
        write_proc(self.root, "701", comm=odd, ppid=KWIN_PID + 1)
        self.assertEqual(follow.find_keyboard_pids(KWIN_PID, odd, UID, proc_root=self.root), [700])

    def test_skips_unreadable_and_vanished_entries_and_keeps_scanning(self):
        write_proc(self.root, "500")  # a real match, so the scan must carry on past the bad entries
        os.makedirs(os.path.join(self.root, "501", "comm"))  # comm is a directory, so reading it fails
        write_proc(self.root, "502")
        os.remove(os.path.join(self.root, "502", "stat"))  # vanished halfway through
        write_proc(self.root, "503")
        os.remove(os.path.join(self.root, "503", "status"))
        write_proc(self.root, "504")
        with open(os.path.join(self.root, "504", "stat"), "w") as f:
            f.write("504 no closing parenthesis\n")
        write_proc(self.root, "505")
        with open(os.path.join(self.root, "505", "status"), "w") as f:
            f.write("Name:\tplasma-keyboard\n")  # no Uid line
        self.assertEqual(self.find(), [500])

    def test_returns_sorted_ints(self):
        for entry in ("900", "100", "500"):
            write_proc(self.root, entry)
        result = self.find()
        self.assertEqual(result, [100, 500, 900])
        self.assertTrue(all(type(pid) is int for pid in result))

    def test_missing_proc_root_returns_an_empty_list(self):
        missing = os.path.join(self.root, "missing")
        self.assertEqual(follow.find_keyboard_pids(KWIN_PID, NAME, UID, proc_root=missing), [])


class RestarterTest(unittest.TestCase):
    def test_kills_exactly_the_found_pids_with_sigkill(self):
        find = FakeFind([300, 301])
        kill = FakeKill()
        restarter = fake_restarter(find, kill, FakeClock(), FakeScheduler())
        out = run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(kill.calls, [(300, signal.SIGKILL), (301, signal.SIGKILL)])
        self.assertEqual(find.queries, [(KWIN_PID, NAME, UID)])
        self.assertIn("restarted the on-screen keyboard (tablet mode on): killed pid 300, 301\n", out)

    def test_no_keyboard_found_kills_nothing_and_does_not_use_up_the_rate_limit(self):
        find = FakeFind([], [300])
        kill = FakeKill()
        restarter = fake_restarter(find, kill, FakeClock(100.0), FakeScheduler())
        out = run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(out, f"no {NAME} process under KWin (pid {KWIN_PID}); nothing to restart\n")
        self.assertEqual(kill.calls, [])
        run_logged(lambda: restarter.restart("tablet mode on"))  # same clock reading, so only the rate limit could skip it
        self.assertEqual(kill.calls, [(300, signal.SIGKILL)])

    def test_unknown_kwin_pid_does_nothing(self):
        find = FakeFind([300])
        kill = FakeKill()
        restarter = follow.Restarter(lambda: None, NAME, UID, find=find, kill=kill, clock=FakeClock(),
                                     schedule=FakeScheduler())
        out = run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(out, "z13-vkeyboard-follow: restart skipped (tablet mode on): KWin pid unknown\n")
        self.assertEqual(find.queries, [])
        self.assertEqual(kill.calls, [])

    def test_a_restart_within_30_seconds_of_the_last_one_is_skipped(self):
        find = FakeFind([300])
        kill = FakeKill()
        clock = FakeClock(100.0)
        restarter = fake_restarter(find, kill, clock, FakeScheduler())
        run_logged(lambda: restarter.restart("tablet mode on"))
        clock.now = 129.5
        out = run_logged(lambda: restarter.restart("resume from sleep"))
        self.assertEqual(out, "z13-vkeyboard-follow: restart skipped (resume from sleep): last restart was 29 s ago\n")
        self.assertEqual(kill.calls, [(300, signal.SIGKILL)])
        self.assertEqual(len(find.queries), 1)

    def test_a_restart_30_seconds_after_the_last_one_is_allowed(self):
        find = FakeFind([300], [301])
        kill = FakeKill()
        clock = FakeClock(100.0)
        restarter = fake_restarter(find, kill, clock, FakeScheduler())
        run_logged(lambda: restarter.restart("tablet mode on"))
        clock.now = 130.0
        run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(kill.calls, [(300, signal.SIGKILL), (301, signal.SIGKILL)])

    def test_a_vanished_or_unkillable_pid_is_logged_and_the_rest_are_still_killed(self):
        find = FakeFind([300, 301, 302])
        kill = FakeKill({300: ProcessLookupError(), 301: PermissionError(1, "Operation not permitted")})
        restarter = fake_restarter(find, kill, FakeClock(), FakeScheduler())
        out = run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(kill.calls, [(300, signal.SIGKILL), (301, signal.SIGKILL), (302, signal.SIGKILL)])
        self.assertEqual(out, "z13-vkeyboard-follow: pid 300 was already gone\n"
                              "z13-vkeyboard-follow: cannot kill pid 301: Operation not permitted\n"
                              "restarted the on-screen keyboard (tablet mode on): killed pid 302\n")

    def test_when_nothing_is_killed_there_is_no_restart_line_and_no_verify(self):
        find = FakeFind([300])
        kill = FakeKill({300: PermissionError(1, "Operation not permitted")})
        scheduler = FakeScheduler()
        restarter = fake_restarter(find, kill, FakeClock(), scheduler)
        out = run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(out, "z13-vkeyboard-follow: cannot kill pid 300: Operation not permitted\n")
        self.assertEqual(scheduler.calls, [])

    def test_verify_reports_a_new_pid_as_back(self):
        find = FakeFind([300], [301])
        scheduler = FakeScheduler()
        restarter = fake_restarter(find, FakeKill(), FakeClock(), scheduler)
        run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual([delay for delay, _ in scheduler.calls], [3000])
        self.assertEqual(run_logged(scheduler.fire_next), "on-screen keyboard is back as pid 301\n")
        self.assertEqual(find.queries[-1], (KWIN_PID, NAME, UID))

    def test_verify_warns_when_no_keyboard_comes_back(self):
        find = FakeFind([300], [])
        scheduler = FakeScheduler()
        restarter = fake_restarter(find, FakeKill(), FakeClock(), scheduler)
        run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(run_logged(scheduler.fire_next), DID_NOT_RETURN.format(pid=300))

    def test_verify_warns_when_the_same_pid_is_still_running(self):
        find = FakeFind([300])
        scheduler = FakeScheduler()
        restarter = fake_restarter(find, FakeKill(), FakeClock(), scheduler)
        run_logged(lambda: restarter.restart("tablet mode on"))
        self.assertEqual(run_logged(scheduler.fire_next), DID_NOT_RETURN.format(pid=300))


class RestartTriggerTest(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus(tablet=False)
        self.keyboard = FakeKeyboard(mode=0)
        self.scheduler = FakeScheduler()
        self.restarter = FakeRestarter()
        self.follower = follow.Follower(self.bus, self.keyboard, 0, 1, schedule=self.scheduler, restarter=self.restarter)

    def refresh(self, tablet):
        self.bus.tablet = tablet
        with contextlib.redirect_stdout(io.StringIO()):
            return self.follower.refresh()

    def sleep_signal(self, going_to_sleep):
        """Send PrepareForSleep the way GLib would, with a (b) parameter."""
        self.follower.on_prepare_for_sleep(None, LOGIND, LOGIND_PATH, LOGIND_IFACE, "PrepareForSleep",
                                           GLib.Variant("(b)", (going_to_sleep,)))

    def test_tablet_mode_turning_on_restarts_once(self):
        self.refresh(False)
        self.refresh(True)
        self.refresh(True)
        self.assertEqual(self.restarter.reasons, ["tablet mode on"])

    def test_the_first_reading_does_not_restart_even_in_tablet_mode(self):
        self.refresh(True)
        self.assertEqual(self.restarter.reasons, [])

    def test_turning_off_does_not_restart_and_turning_on_again_does(self):
        self.refresh(True)
        self.refresh(False)
        self.assertEqual(self.restarter.reasons, [])
        self.refresh(True)
        self.assertEqual(self.restarter.reasons, ["tablet mode on"])

    def test_no_restarter_configured_never_restarts_and_does_not_crash(self):
        self.follower = follow.Follower(self.bus, self.keyboard, 0, 1, schedule=self.scheduler)
        self.refresh(False)
        self.refresh(True)
        self.assertEqual(self.keyboard.mode, 1)
        self.sleep_signal(False)
        self.assertEqual(self.scheduler.calls, [])

    def test_a_failed_read_does_not_update_the_last_value(self):
        self.refresh(False)
        self.bus.error = GLib.Error("name not provided")
        self.assertFalse(self.refresh(True))
        self.assertIs(self.follower.last_tablet, False)
        self.bus.error = None
        self.assertTrue(self.refresh(True))
        self.assertEqual(self.restarter.reasons, ["tablet mode on"])

    def test_a_failed_write_is_retried_and_the_retry_restarts_once(self):
        self.refresh(False)
        self.keyboard.set_error = GLib.Error("access denied")
        self.bus.tablet = True
        run_logged(self.follower.on_tablet_signal)
        self.assertIs(self.follower.last_tablet, False)
        self.assertEqual(self.restarter.reasons, [])
        self.keyboard.set_error = None
        run_logged(self.scheduler.fire_next)  # the retry
        self.assertEqual(self.restarter.reasons, ["tablet mode on"])
        self.assertIs(self.follower.last_tablet, True)

    def test_kwin_vanishing_forgets_the_last_value_so_its_return_does_not_restart(self):
        self.refresh(False)
        self.follower.on_kwin_vanished(None, "org.kde.KWin", "")
        self.refresh(True)
        self.assertEqual(self.restarter.reasons, [])

    def test_resume_in_tablet_mode_schedules_a_restart_after_2000_ms(self):
        self.refresh(True)
        self.sleep_signal(False)
        self.assertEqual([delay for delay, _ in self.scheduler.calls], [2000])
        self.assertEqual(self.restarter.reasons, [])
        self.scheduler.fire_next()
        self.assertEqual(self.restarter.reasons, ["resume from sleep"])

    def test_going_to_sleep_does_nothing(self):
        self.refresh(True)
        self.sleep_signal(True)
        self.assertEqual(self.scheduler.calls, [])

    def test_resume_outside_tablet_mode_does_nothing(self):
        self.sleep_signal(False)  # no reading yet
        self.refresh(False)
        self.sleep_signal(False)
        self.assertEqual(self.scheduler.calls, [])
        self.assertEqual(self.restarter.reasons, [])

    def test_tablet_mode_ending_during_the_settle_cancels_the_resume_restart(self):
        self.refresh(True)
        self.sleep_signal(False)
        self.refresh(False)
        self.scheduler.fire_next()
        self.assertEqual(self.restarter.reasons, [])

    def test_subscribe_sleep_listens_for_prepare_for_sleep_on_logind(self):
        bus = FakeSignalBus()
        self.follower.subscribe_sleep(bus)
        self.assertEqual(bus.subscriptions, [(LOGIND, LOGIND_IFACE, "PrepareForSleep", LOGIND_PATH, None,
                                              self.follower.on_prepare_for_sleep)])


class KWinProcessTest(unittest.TestCase):
    def test_asks_the_bus_daemon_for_the_pid_of_the_kwin_name(self):
        daemon = FakeDaemon(pid=KWIN_PID)
        self.assertEqual(follow.KWinProcess(daemon).pid(), KWIN_PID)
        self.assertEqual(daemon.calls, [("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                         "GetConnectionUnixProcessID", ("org.kde.KWin",))])

    def test_a_glib_error_means_the_pid_is_unknown(self):
        daemon = FakeDaemon(error=GLib.Error("name has no owner"))
        self.assertIsNone(follow.KWinProcess(daemon).pid())


class RestartSettingsTest(unittest.TestCase):
    def test_defaults_are_on_and_plasma_keyboard(self):
        self.assertEqual(follow.load_restart_settings({}), (True, "plasma-keyboard"))

    def test_zero_turns_restarts_off_and_one_turns_them_on(self):
        self.assertEqual(follow.load_restart_settings({RESTART: "0"}), (False, "plasma-keyboard"))
        self.assertEqual(follow.load_restart_settings({RESTART: "1"}), (True, "plasma-keyboard"))

    def test_process_name_can_be_changed(self):
        self.assertEqual(follow.load_restart_settings({PROCESS: "my-keyboard"}), (True, "my-keyboard"))

    def test_other_restart_values_are_rejected_and_name_the_variable(self):
        for bad in ("", "on", "2", "true", "01"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError) as ctx:
                    follow.load_restart_settings({RESTART: bad})
                self.assertIn(RESTART, str(ctx.exception))

    def test_empty_process_name_is_rejected_and_names_the_variable(self):
        with self.assertRaises(ValueError) as ctx:
            follow.load_restart_settings({PROCESS: ""})
        self.assertIn(PROCESS, str(ctx.exception))


class ImportTest(unittest.TestCase):
    def test_importing_the_module_does_not_touch_the_bus(self):
        with mock.patch.object(Gio, "bus_get_sync") as get_sync, \
                mock.patch.object(Gio, "bus_get") as get_async, \
                mock.patch.object(Gio, "bus_watch_name") as watch, \
                mock.patch.object(GLibUnix, "signal_add") as signal_add, \
                mock.patch.object(GLib, "timeout_add") as timeout_add:
            load_module("z13_vkeyboard_follow_import_check")
        for fake in (get_sync, get_async, watch, signal_add, timeout_add):
            fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
