"""Hermetic unit tests for z13-vkeyboard-follow.py: fake keyboards, fake buses and fake schedulers only, and nothing here touches the real bus."""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import unittest
from unittest import mock

from gi.repository import Gio, GLib, GLibUnix

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "z13-vkeyboard-follow.py")
ATTACHED = "Z13_VKEYBOARD_ATTACHED_MODE"
DETACHED = "Z13_VKEYBOARD_DETACHED_MODE"


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
