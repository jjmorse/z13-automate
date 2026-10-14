"""Unit tests for external_keyboards() in z13-onboard-follow.py."""

import importlib.machinery
import importlib.util
import os
import unittest

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "z13-onboard-follow.py")
LOADER = importlib.machinery.SourceFileLoader("follow", PATH)
SPEC = importlib.util.spec_from_loader("follow", LOADER)
follow = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(follow)

USB_KEYBOARD_IF1 = """P: /devices/pci0000:00/0000:00:08.1/usb11/11-2/11-2:1.0/0003:1234:0001.0003/input/input60/event30
E: DEVNAME=/dev/input/event30
E: DEVPATH=/devices/pci0000:00/0000:00:08.1/usb11/11-2/11-2:1.0/0003:1234:0001.0003/input/input60/event30
E: ID_INPUT_KEYBOARD=1
E: ID_BUS=usb
E: ID_MODEL=Example_Keyboard
E: ID_VENDOR_ID=1234
E: ID_MODEL_ID=0001"""

USB_KEYBOARD_IF2 = USB_KEYBOARD_IF1.replace("event30", "event31").replace("input60", "input61")

I8042_KEYBOARD = """P: /devices/platform/i8042/serio0/input/input3/event2
E: DEVNAME=/dev/input/event2
E: DEVPATH=/devices/platform/i8042/serio0/input/input3/event2
E: ID_INPUT_KEYBOARD=1
E: ID_BUS=i8042
E: ID_MODEL=Example_Internal_Keyboard
E: ID_VENDOR_ID=5678
E: ID_MODEL_ID=0003"""

VIRTUAL_KEYBOARD = """P: /devices/virtual/input/input48/event4
E: DEVNAME=/dev/input/event4
E: DEVPATH=/devices/virtual/input/input48/event4
E: ID_INPUT_KEYBOARD=1
E: ID_BUS=usb
E: ID_MODEL=Example_Virtual_Keyboard
E: ID_VENDOR_ID=5678
E: ID_MODEL_ID=0004"""

ASUS_KEYBOARD = """P: /devices/pci0000:00/0000:00:08.1/usb11/11-3/11-3:1.0/0003:0B05:1A2B.0005/input/input62/event33
E: DEVNAME=/dev/input/event33
E: DEVPATH=/devices/pci0000:00/0000:00:08.1/usb11/11-3/11-3:1.0/0003:0B05:1A2B.0005/input/input62/event33
E: ID_INPUT_KEYBOARD=1
E: ID_BUS=usb
E: ID_MODEL=Example_Folio
E: ID_VENDOR_ID=0b05
E: ID_MODEL_ID=1a2b"""

BT_KEYBOARD = """P: /devices/platform/example_bt/bluetooth/hci0/hci0:11/0005:5678:0002.0006/input/input70/event12
E: DEVNAME=/dev/input/event12
E: DEVPATH=/devices/platform/example_bt/bluetooth/hci0/hci0:11/0005:5678:0002.0006/input/input70/event12
E: ID_INPUT_KEYBOARD=1
E: ID_BUS=bluetooth
E: ID_MODEL=Example_BT_Keyboard
E: ID_VENDOR_ID=5678
E: ID_MODEL_ID=0002"""

MOUSE = """P: /devices/pci0000:00/0000:00:08.1/usb11/11-4/11-4:1.0/0003:1234:0003.0007/input/input75/event20
E: DEVNAME=/dev/input/event20
E: DEVPATH=/devices/pci0000:00/0000:00:08.1/usb11/11-4/11-4:1.0/0003:1234:0003.0007/input/input75/event20
E: ID_INPUT_MOUSE=1
E: ID_BUS=usb
E: ID_MODEL=Example_Mouse
E: ID_VENDOR_ID=1234
E: ID_MODEL_ID=0003"""

NO_MODEL_KEYBOARD = """P: /devices/pci0000:00/0000:00:08.1/usb11/11-5/11-5:1.0/0003:1234:0009.0008/input/input80/event40
E: DEVNAME=/dev/input/event40
E: DEVPATH=/devices/pci0000:00/0000:00:08.1/usb11/11-5/11-5:1.0/0003:1234:0009.0008/input/input80/event40
E: ID_INPUT_KEYBOARD=1
E: ID_BUS=usb
E: ID_VENDOR_ID=1234
E: ID_MODEL_ID=0009"""


class ExternalKeyboardsTest(unittest.TestCase):
    def test_usb_keyboard_with_two_interfaces_is_one_name(self):
        text = "\n\n".join([USB_KEYBOARD_IF1, USB_KEYBOARD_IF2])
        self.assertEqual(follow.external_keyboards(text), ["Example Keyboard"])

    def test_internal_i8042_keyboard_is_ignored(self):
        self.assertEqual(follow.external_keyboards(I8042_KEYBOARD), [])

    def test_virtual_device_is_ignored(self):
        self.assertEqual(follow.external_keyboards(VIRTUAL_KEYBOARD), [])

    def test_asus_vendor_is_ignored(self):
        self.assertEqual(follow.external_keyboards(ASUS_KEYBOARD), [])

    def test_bluetooth_keyboard_is_included(self):
        self.assertEqual(follow.external_keyboards(BT_KEYBOARD), ["Example BT Keyboard"])

    def test_mouse_is_ignored(self):
        self.assertEqual(follow.external_keyboards(MOUSE), [])

    def test_empty_text_gives_empty_list(self):
        self.assertEqual(follow.external_keyboards(""), [])

    def test_missing_model_falls_back_to_vendor_and_model_id(self):
        self.assertEqual(follow.external_keyboards(NO_MODEL_KEYBOARD), ["1234:0009"])

    def test_mixed_db_gives_sorted_unique_names(self):
        text = "\n\n".join([USB_KEYBOARD_IF1, USB_KEYBOARD_IF2, I8042_KEYBOARD, VIRTUAL_KEYBOARD,
                             ASUS_KEYBOARD, BT_KEYBOARD, MOUSE])
        self.assertEqual(follow.external_keyboards(text), ["Example BT Keyboard", "Example Keyboard"])


if __name__ == "__main__":
    unittest.main()
