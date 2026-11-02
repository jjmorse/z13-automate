# z13-vkeyboard-follow

This folder keeps Plasma Keyboard, KDE's on-screen keyboard, hidden while the keyboard folio is attached to an ASUS ROG Flow Z13, and shows it for touch when the folio is detached. It also restarts Plasma Keyboard when it has gone stale, which can happen after days of uptime; see Restarts of a stale keyboard below. It is tested on an ASUS ROG Flow Z13 (GZ302EA) with CachyOS and KDE Plasma 6.8 on Wayland, and on that setup only.

## What was wrong

Plasma Keyboard appears for touch input even while the keyboard folio is attached, so it covers part of the screen on a laptop that is being used with its keyboard.

## How it works

KWin has a tablet-mode flag (`org.kde.KWin.TabletModeManager`, property `tabletMode`) that turns on when the folio is detached. KWin also has a virtual keyboard mode (`org.kde.kwin.VirtualKeyboard`, property `mode`) that decides when the on-screen keyboard shows.

The user service `z13-vkeyboard-follow` watches the tablet-mode flag and sets the virtual keyboard mode to match. With the folio attached it sets mode 0 (never show). With the folio detached it sets mode 1 (show for touch and pen only). It applies the value when KWin starts, again 1.5 seconds later, and again on every change of the flag. A failed read or write is retried up to 3 times, 2 seconds apart, and then the watcher waits for the next event. It writes only when the value differs. It also restarts a stale Plasma Keyboard process when tablet mode turns on, and when the system resumes from sleep while tablet mode is on, as described in Restarts of a stale keyboard.

## Restarts of a stale keyboard

Plasma Keyboard can stop appearing in every app after about two days of uptime with many suspends and dock changes, and a fresh process fixes it. The cause is not known. KWin relaunches an input method only after it exits from a crash, and a normal stop does not count, so the watcher sends SIGKILL, which KWin counts as a crash.

It restarts the keyboard in two cases:

- Tablet mode turns on, which means the folio was detached. It does not restart at service start, when KWin appears, or when tablet mode turns off.
- The system resumes from sleep while tablet mode is on. The restart waits 2 seconds after the resume, so the devices and displays have settled.

How it restarts:

- It sends SIGKILL only to the keyboard process: a direct child of KWin, with the name in `Z13_VKEYBOARD_PROCESS` and your user as its real user. Nothing else is signalled.
- KWin starts a new keyboard process within about a second.
- There is at most one restart per 30 seconds. A request inside that window is logged and skipped.
- About 3 seconds after the kill, the watcher checks for a keyboard process with a new pid.

What you will notice:

- The keyboard blinks for about a second at detach and at wake.
- Any half-typed word in the keyboard is lost.

How to check it: each restart logs `restarted the on-screen keyboard (<reason>): killed pid <pid>`, and the check 3 seconds later logs `on-screen keyboard is back as pid <pid>`, or a warning if no keyboard process came back. Show those lines with:

```bash
journalctl --user -u z13-vkeyboard-follow.service | grep -E 'restarted the on-screen keyboard|on-screen keyboard is back'
```

How to turn restarts off: open a drop-in for the service:

```bash
systemctl --user edit z13-vkeyboard-follow.service
```

Add these lines in the editor, then save and close it:

```ini
[Service]
Environment=Z13_VKEYBOARD_RESTART=0
```

Restart the service so it reads the new value:

```bash
systemctl --user restart z13-vkeyboard-follow.service
```

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
| `Z13_VKEYBOARD_RESTART` | 1 | a stale keyboard is restarted on tablet mode on and on resume in tablet mode (0 turns restarts off, 1 turns them on) |
| `Z13_VKEYBOARD_PROCESS` | plasma-keyboard | a restart is made (the process name as /proc shows it, at most 15 characters) |

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
- The cause of the stale keyboard is not known, so the restart is a workaround.
- A restart on resume happens only in tablet mode.
- The tests cover the mode, retry and restart logic with fakes. The live D-Bus signals and the kill path are checked by hand.
