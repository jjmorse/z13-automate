# z13-vkeyboard-follow

This folder keeps Plasma Keyboard, KDE's on-screen keyboard, hidden while the keyboard folio is attached to an ASUS ROG Flow Z13, and shows it for touch when the folio is detached. It is tested on an ASUS ROG Flow Z13 (GZ302EA) with CachyOS and KDE Plasma 6.8 on Wayland, and on that setup only.

## What was wrong

Plasma Keyboard appears for touch input even while the keyboard folio is attached, so it covers part of the screen on a laptop that is being used with its keyboard.

## How it works

KWin has a tablet-mode flag (`org.kde.KWin.TabletModeManager`, property `tabletMode`) that turns on when the folio is detached. KWin also has a virtual keyboard mode (`org.kde.kwin.VirtualKeyboard`, property `mode`) that decides when the on-screen keyboard shows.

The user service `z13-vkeyboard-follow` watches the tablet-mode flag and sets the virtual keyboard mode to match. With the folio attached it sets mode 0 (never show). With the folio detached it sets mode 1 (show for touch and pen only). It applies the value when KWin starts, again 1.5 seconds later, and again on every change of the flag. A failed read or write is retried up to 3 times, 2 seconds apart, and then the watcher waits for the next event. It writes only when the value differs, and it does not restart or signal Plasma Keyboard.

## Install

The watcher needs PyGObject, which provides `gi`. Install it first:

```bash
sudo pacman -S --needed python-gobject
```

Preview the installer from the repository root. It changes nothing:

```bash
bash linux/z13-vkeyboard-follow/install.sh --dry-run
```

Install, as your normal user. The installer refuses to run as root:

```bash
bash linux/z13-vkeyboard-follow/install.sh
```

The installer copies the watcher to `~/.local/bin/z13-vkeyboard-follow`, copies the unit to `~/.config/systemd/user/`, and enables the service now and at login.

## Check it works

Show the service's recent log lines. There is one line per decision, and `already` means no write was needed:

```bash
journalctl --user -u z13-vkeyboard-follow.service -n 20
```

Read KWin's current virtual keyboard mode:

```bash
qdbus6 org.kde.KWin /VirtualKeyboard org.freedesktop.DBus.Properties.Get org.kde.kwin.VirtualKeyboard mode
```

Detach the folio, wait a second, and run the same command. It should print 1. Reattach the folio, wait a second, and run it again. It should print 0.

## Settings

| Variable | Default | Used when |
| --- | --- | --- |
| `Z13_VKEYBOARD_ATTACHED_MODE` | 0 | the folio is attached (0 never show, 1 touch and pen only, 2 every input device) |
| `Z13_VKEYBOARD_DETACHED_MODE` | 1 | the folio is detached (same values) |

To change one, open a drop-in for the service:

```bash
systemctl --user edit z13-vkeyboard-follow.service
```

Add these lines in the editor, then save and close it:

```ini
[Service]
Environment=Z13_VKEYBOARD_DETACHED_MODE=2
```

Restart the service so it reads the new value:

```bash
systemctl --user restart z13-vkeyboard-follow.service
```

An invalid value makes the service exit with status 2, which the unit does not restart on, so it stays stopped until you fix the drop-in and restart it.

## Uninstall

Run this from the repository root:

```bash
bash linux/z13-vkeyboard-follow/install.sh --uninstall
```

The uninstall stops and disables the service, and removes the watcher and the unit file. KWin keeps the last virtual keyboard mode the watcher set.

## Known limits

- The folio must really be detached for the tablet-mode flag to change. If the flag does not follow the folio, the watcher cannot help.
- The mode numbers come from KWin and may change between Plasma versions. The two environment variables exist for that reason.
- The watcher only sets KWin's mode. It does not restart or signal Plasma Keyboard.
- The tests cover only the mode and retry logic. The D-Bus path is checked by hand with the commands above.
