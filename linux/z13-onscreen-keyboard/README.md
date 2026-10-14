# z13-onscreen-keyboard

This folder sets up Onboard, an on-screen keyboard with Tab, Ctrl and arrow keys, as the touch keyboard. Onboard shows itself when a text field is tapped with the keyboard folio detached (tablet mode), and stays hidden while the folio is attached. It is tested on an ASUS ROG Flow Z13 (GZ302EA) with CachyOS, KDE Plasma 6.7 on Wayland and Onboard 1.4.1, and on that setup only.

## What was wrong

Plasma Keyboard, the virtual keyboard KDE shows for touch input, has no Tab, Ctrl or arrow keys. Onboard has them and can show itself when an editable field is focused, but it also shows itself while the folio is attached, and it does not work on Wayland out of the box.

## How it works

Onboard runs under XWayland (`GDK_BACKEND=x11`) with GTK input events and uinput for keystrokes. Desktop accessibility is turned on so that apps report their text fields to Onboard.

KWin's tablet-mode flag (`org.kde.KWin.TabletModeManager`) turns on when the folio is detached. The watcher, `z13-onboard-follow`, follows that flag. In tablet mode, with no external keyboard connected, it sets Onboard's `AutoShowPaused` property to false, so auto-show works. With the folio attached it sets `AutoShowPaused` to true and hides the keyboard. The user unit `z13-onboard.service` starts Onboard, and `z13-onboard-follow.service` runs the watcher.

The watcher also looks for an external physical keyboard (a USB or Bluetooth keyboard other than the Z13's own, found in the udev database) and keeps auto-show paused while one is connected, even in tablet mode, and it reacts to keyboards being plugged in or removed.

Onboard also has its own keyboard-detection option, which is not used. Under X11 mode Onboard cannot see the real input devices, and the Z13 has two permanent internal keyboard devices, so that option cannot tell whether the folio is attached.

## Install

Install Onboard first:

```bash
sudo pacman -S --needed onboard
```

Preview the installer from the repository root. It does not change anything:

```bash
bash linux/z13-onscreen-keyboard/install.sh --dry-run
```

Install, as your normal user. The installer refuses to run as root:

```bash
bash linux/z13-onscreen-keyboard/install.sh
```

## Check it works

Show the tablet-mode flag and Onboard's `AutoShowPaused` value:

```bash
~/.local/bin/z13-onboard-follow --status
```

Detach the folio and tap a text field. The Onboard keyboard should appear. Reattach the folio and tap a text field again. Nothing should appear. Apps that were already running before accessibility was turned on need a restart once.

With the folio detached and an external keyboard plugged in, tapping a text field should not bring up Onboard, and `z13-onboard-follow --status` lists the keyboard.

## Settings it applies

| Schema | Key | Value |
| --- | --- | --- |
| `org.onboard.keyboard` | `input-event-source` | `GTK` |
| `org.onboard.keyboard` | `key-synth` | `uinput` |
| `org.onboard.auto-show` | `enabled` | `true` |
| `org.gnome.desktop.interface` | `toolkit-accessibility` | `true` |

## Uninstall

Run this from the repository root:

```bash
bash linux/z13-onscreen-keyboard/install.sh --uninstall
```

The uninstall stops and disables both units, removes the watcher script and the two unit files, and removes the autostart override only if it still has the content the installer wrote. The gsettings values are left as they are. To reset one, run:

```bash
gsettings reset org.onboard.auto-show enabled
```

## Known limits

- Apps opened before accessibility was turned on need a restart before they report their text fields.
- Chromium and Electron apps, and Wayland-native apps, may need their own accessibility switch. Untested.
- Onboard under Wayland is a workaround through XWayland. One report says it can crash on close; the service restarts it.
- The lock screen and the login screen are not covered.
- Plasma Keyboard stays installed but is not used while Onboard is the only thing started.
- Tests cover only the keyboard-detection parser (`python3 -I -m unittest discover -v -s linux/z13-onscreen-keyboard`); the D-Bus and udev monitor paths are checked with `--status`, `--once` and by hand.
- A keyboard that reports itself as a virtual or non-keyboard device (some wireless receivers) is not detected, and the ASUS vendor id (0b05) is ignored on purpose.
