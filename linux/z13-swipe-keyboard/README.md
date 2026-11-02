# z13-swipe-keyboard

This package shows the on-screen keyboard when you swipe up from the bottom edge of the touchscreen. It is a KWin script for KDE Plasma 6. It is written for an ASUS ROG Flow Z13 (GZ302EA); other setups are untested.

## What it does

The script registers a touch screen edge on the bottom edge of the screen (`KWin.ElectricBottom`) with KWin's `registerTouchScreenEdge`. When you swipe up from that edge, the script calls `forceActivate` on KWin's virtual keyboard object (`/VirtualKeyboard`, interface `org.kde.kwin.VirtualKeyboard`) through `callDBus`, and the keyboard appears.

## Why a KWin script

KDE has no built-in screen-edge action that shows the keyboard, so a script is the only user-level way to add one. The script needs no input-group access. Reading the touchscreen directly would need the input group, which also lets a program read every keystroke.

## Requirements

- KDE Plasma 6 with KWin's virtual keyboard enabled, for example Plasma Keyboard.
- `kpackagetool6`, `kwriteconfig6` and `qdbus6`. The installer warns about any that is missing and skips the step that needs it.

## Install

Preview the installer from the repository root. It changes nothing:

```bash
bash linux/z13-swipe-keyboard/install.sh --dry-run
```

Install as your normal user. The installer refuses to run as root:

```bash
bash linux/z13-swipe-keyboard/install.sh
```

The installer installs the package with `kpackagetool6` (it uses `--upgrade` when the package is already installed), turns on `z13swipekeyboardEnabled` in the `[Plugins]` group of `kwinrc`, and reloads KWin with `qdbus6 org.kde.KWin /KWin reconfigure`. It installs no system files and adds no packages.

## Check it works

List the installed KWin scripts. The package should appear as `z13swipekeyboard`:

```bash
kpackagetool6 --type=KWin/Script --list
```

Show the user journal lines that mention the script. The script writes no log lines of its own, so this command usually prints nothing:

```bash
journalctl --user -b | grep -i z13swipe
```

To test the gesture, swipe up from the bottom edge of the touchscreen. The keyboard should appear.

## Uninstall

Run this from the repository root:

```bash
bash linux/z13-swipe-keyboard/install.sh --uninstall
```

The uninstall turns the setting off, reloads KWin, and removes the package with `kpackagetool6 --remove z13swipekeyboard`.

## Options

| Option | Meaning |
| --- | --- |
| `--dry-run` | Print each command and change nothing |
| `--uninstall` | Turn the script off and remove it |
| `-h`, `--help` | Print the help |

## Known limits

- It needs KWin's virtual keyboard enabled, for example Plasma Keyboard. Without it, `forceActivate` has nothing to show.
- It responds to touch only. A mouse or touchpad does not trigger it.
- The swipe starts at the physical bottom edge, so it can conflict with a bottom panel's touch handling.
- The KWin scripting API has no unload hook, so the script cannot unregister its edge when it is removed. This is untested on the device.
- It is unverified on every device. Confirm the swipe on your own touchscreen, and confirm that `forceActivate` shows the keyboard while the virtual keyboard mode is 0.
