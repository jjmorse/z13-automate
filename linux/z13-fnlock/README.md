# z13-fnlock

Fn lock for the ASUS ROG Flow Z13 (GZ302EA) folio keyboard on Linux. Pressing Fn+Esc toggles whether F1 to F12 are plain F-keys or media keys.

Tested on one machine: a 2025 ROG Flow Z13 (GZ302EA) with the folio keyboard (USB `0b05:1a30`), CachyOS, kernel 7.2.9. Other Z13 models and keyboards are untested. It is a stopgap for a missing kernel quirk, see Upstream below.

## Why it exists

The keyboard sends Fn+Esc to the kernel as `KEY_FN_ESC` (scancode `ff31004e`), but the kernel's `hid-asus` driver only acts on it for keyboards flagged `QUIRK_HID_FN_LOCK`, and the Z13 folio is not flagged. This service sends the same 64-byte HID feature report the driver would send: report id `0x5a`, then `d0 4e`, then `01` (on) or `00` (off). Sending it by hand to the keyboard's vendor interface switched the function row between plain F-keys and media keys, so the firmware accepts it.

## States

- Fn lock ON: F1 to F12 are plain F-keys.
- Fn lock OFF: the row sends media keys (volume, brightness and so on), and Fn is needed for F-keys.

The default is OFF (media keys), set with `--default off` in `z13-fnlock.service`. Change it to `--default on` and restart the service if you want F-keys by default.

## Install

Preview first, then install. The install needs sudo and Python 3, and uses systemd.

```bash
bash linux/z13-fnlock/install.sh --dry-run
```

```bash
sudo bash linux/z13-fnlock/install.sh
```

It installs `/usr/local/bin/z13-fnlock` and two systemd units, enables both, and starts the service. Then press Fn+Esc and watch the log:

```bash
journalctl -u z13-fnlock -f
```

## What it does

- Watches the keyboard's input node for Fn+Esc and toggles the state. It does not grab the device, so other software still sees the key.
- Applies the default state when the service starts and again whenever the keyboard appears (the folio detaches and reattaches). A toggle you made is remembered while the service runs, and reapplied after a reattach.
- Reapplies the state after suspend or hibernate: `z13-fnlock-resume.service` sends `SIGUSR1` to the daemon.
- Ignores a second Fn+Esc within 0.3 seconds, in case two input nodes report the same press.
- If sending fails (for example the keyboard is still starting up), it logs once and retries every 2 seconds.

## Commands

- `z13-fnlock find` lists the keyboard's nodes and the node it would send to. It needs no root and sends nothing.
- `sudo z13-fnlock on` or `off` sets the state once.
- `z13-fnlock daemon [--default on|off]` is what the service runs.

## Uninstall

```bash
sudo bash linux/z13-fnlock/install.sh --uninstall
```

## Tests

```bash
python3 -I -m unittest discover -v -s linux/z13-fnlock -p 'test_*.py'
```

The unit tests use fake devices and need no hardware. One test reads the real sysfs and is skipped when the keyboard is not attached.

## Known limits

- The unit tests cover the logic, and the manual `on` and `off` test confirmed the firmware accepts the report. Not verified: behavior across a real suspend and resume, a real detach and reattach, and whether the keyboard remembers its state across a power cycle.
- The service runs as root, because the input and hidraw nodes are root-only. It only reads key events from the Z13 folio and writes one feature report to it.

## Upstream

The proper fix is one line in the kernel. In `drivers/hid/hid-asus.c`, the table entry for `USB_DEVICE_ID_ASUSTEK_ROG_Z13_FOLIO` (`0x1a30`) needs `QUIRK_HID_FN_LOCK` added to its quirks:

```diff
 	{ HID_DEVICE(BUS_USB, HID_GROUP_GENERIC,
 		USB_VENDOR_ID_ASUSTEK, USB_DEVICE_ID_ASUSTEK_ROG_Z13_FOLIO),
-	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD },
+	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD | QUIRK_HID_FN_LOCK },
```

That flag is what makes the driver toggle Fn lock on `KEY_FN_ESC` (and set it ON at attach, so F-keys would be the default). As of 2026-10-12 this has not been built into a kernel, tested in-kernel, or sent upstream, and the entry above is from upstream master, not the CachyOS kernel. Once a kernel you run does this, stop and uninstall this service.
