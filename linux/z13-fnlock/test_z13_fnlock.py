import importlib.util
import os
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("z13_fnlock", Path(__file__).with_name("z13-fnlock.py"))
fnlock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fnlock)


def event(code, value, etype=fnlock.EV_KEY):
    return fnlock.EVENT.pack(0, 0, etype, code, value)


class FakeKeyboard:
    """A pipe standing in for an input event node, plus the hooks the daemon needs."""

    def __init__(self):
        self.paths = []
        self.pipes = {}
        self.sent = []
        self.fail_next = 0
        self.now = 100.0
        self.messages = []

    def plug(self, path="/dev/input/event99"):
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.pipes[path] = (read_fd, write_fd)
        if path not in self.paths:
            self.paths.append(path)

    def unplug(self, path="/dev/input/event99"):
        read_fd, write_fd = self.pipes.pop(path)
        os.close(write_fd)
        self.paths.remove(path)

    def press(self, code=fnlock.KEY_FN_ESC, value=1, path="/dev/input/event99", etype=fnlock.EV_KEY):
        os.write(self.pipes[path][1], event(code, value, etype))

    def find_inputs(self):
        return list(self.paths)

    def opener(self, path):
        return self.pipes[path][0]

    def send(self, enabled):
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("not ready")
        self.sent.append(enabled)
        return "/dev/hidrawX"

    def clock(self):
        return self.now

    def daemon(self, default=False):
        return fnlock.Daemon(default, send=self.send, find_inputs=self.find_inputs, opener=self.opener,
                             logger=self.messages.append, clock=self.clock)


class KeyBitmapTests(unittest.TestCase):
    def words_for(self, *codes):
        value = 0
        for code in codes:
            value |= 1 << code
        count = 8
        return " ".join("%x" % ((value >> (64 * i)) & (2**64 - 1)) for i in reversed(range(count)))

    def test_fn_esc_bit_found(self):
        self.assertTrue(fnlock.key_bit_set(self.words_for(465, 1), 465))

    def test_other_bits_not_reported(self):
        bitmap = self.words_for(1, 59)
        self.assertFalse(fnlock.key_bit_set(bitmap, 465))
        self.assertTrue(fnlock.key_bit_set(bitmap, 59))

    def test_short_bitmap_has_no_high_bits(self):
        self.assertFalse(fnlock.key_bit_set("ffffffff", 465))

    def test_ioctl_number(self):
        self.assertEqual(hex(fnlock.hidiocsfeature(64)), "0xc0404806")


class DaemonTests(unittest.TestCase):
    def test_default_state_applied_once_when_keyboard_appears(self):
        kb = FakeKeyboard()
        d = kb.daemon(default=False)
        d.step(0)
        self.assertEqual(kb.sent, [])
        kb.plug()
        d.step(0)
        d.step(0)
        self.assertEqual(kb.sent, [False])

    def test_fn_esc_toggles_and_sends(self):
        kb = FakeKeyboard()
        kb.plug()
        d = kb.daemon(default=False)
        d.step(0)
        kb.press()
        d.step(0.05)
        d.step(0)
        kb.now += 1
        kb.press()
        d.step(0.05)
        d.step(0)
        self.assertEqual(kb.sent, [False, True, False])

    def test_release_and_other_keys_are_ignored(self):
        kb = FakeKeyboard()
        kb.plug()
        d = kb.daemon(default=False)
        d.step(0)
        kb.press(value=0)
        kb.press(code=1)
        kb.press(code=fnlock.KEY_FN_ESC, etype=4)
        d.step(0.05)
        d.step(0)
        self.assertEqual(kb.sent, [False])

    def test_duplicate_press_inside_debounce_window_is_ignored(self):
        kb = FakeKeyboard()
        kb.plug()
        d = kb.daemon(default=False)
        d.step(0)
        kb.press()
        kb.now += 0.05
        kb.press()
        d.step(0.05)
        d.step(0)
        self.assertEqual(kb.sent, [False, True])

    def test_reattach_reapplies_current_state(self):
        kb = FakeKeyboard()
        kb.plug()
        d = kb.daemon(default=False)
        d.step(0)
        kb.press()
        d.step(0.05)
        d.step(0)
        self.assertEqual(kb.sent, [False, True])
        kb.unplug()
        d.step(0)
        self.assertEqual(d.fds, {})
        kb.plug()
        d.step(0)
        d.step(0)
        self.assertEqual(kb.sent, [False, True, True])

    def test_failed_send_is_retried(self):
        kb = FakeKeyboard()
        kb.plug()
        kb.fail_next = 2
        d = kb.daemon(default=False)
        d.step(0)
        d.step(0)
        d.step(0)
        self.assertEqual(kb.sent, [False])
        self.assertEqual(len([m for m in kb.messages if m.startswith("could not set Fn lock")]), 1)

    def test_reapply_flag_sends_again(self):
        kb = FakeKeyboard()
        kb.plug()
        d = kb.daemon(default=True)
        d.step(0)
        d.reapply = True
        d.step(0)
        self.assertEqual(kb.sent, [True, True])


class LiveDiscoveryTests(unittest.TestCase):
    """Read-only checks against the real machine. Skipped when the keyboard is not attached."""

    @unittest.skipUnless(fnlock.find_hidraw_nodes(), "Z13 folio keyboard not attached")
    def test_finds_one_vendor_interface_and_an_input_node(self):
        self.assertIsNotNone(fnlock.find_hidraw())
        self.assertTrue(fnlock.find_input_nodes())


if __name__ == "__main__":
    unittest.main()
