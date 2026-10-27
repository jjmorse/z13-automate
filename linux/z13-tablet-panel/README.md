# z13-tablet-panel

This package makes the laptop's Plasma panel taller for touch while the Z13 is in tablet mode and the laptop is the only connected screen. When tablet mode ends, when another screen is connected, or when the service stops, it puts the saved height back. It is written for an ASUS ROG Flow Z13 (GZ302EA) running KDE Plasma 6; other setups are untested.

## What it does

KWin reports tablet mode on the session bus as the `tabletMode` property of `org.kde.KWin.TabletModeManager`, and sends `tabletModeChanged` when it changes. Tablet mode is on while the keyboard folio is detached. The service wants a tablet-sized panel when tablet mode is on and exactly one screen is connected. It then sets the panel height through plasmashell's scripting interface (`org.kde.PlasmaShell.evaluateScript`). The change applies live, without restarting plasmashell.

## Why only the laptop's panel, and only when it is alone

- Only the laptop's panel is changed. With a dock attached, screen 0 is another monitor, so the laptop's panel is no longer the one on screen 0.
- The service reads and writes the panel only while the laptop is the only connected screen. On Plasma 6.8, calling plasmashell's `panels()` function crashes plasmashell when a panel belongs to a disconnected screen. The service never calls `panels()`, and it never reads a panel while another screen is connected.

## How the panel is found

The service reads `~/.config/plasma-org.kde.plasma.desktop-appletsrc`. A panel is a containment section `[Containments][<id>]` with `plugin=org.kde.panel` and `lastScreen=0`. The id must be a whole number. If there is no such panel, or more than one, the service logs one line and leaves the panel alone.

## Saved height and recovery

Before its first change, the service writes `~/.local/state/z13-tablet-panel/state.json` with the panel id and the height it found there, for example:

```json
{"panel_id": 23, "original_height": 30}
```

The file is never overwritten while it exists, so after a restart or a crash the true original height is still known. When tablet mode ends and the laptop is the only screen, the service restores the saved height and deletes the file. A stop (SIGTERM or SIGINT) restores the height the same way. If the laptop is not the only screen at that moment, the file is kept, and the restore happens on the next check once the laptop is alone again while the service is running.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--tablet-height N` | 48 | Panel height in tablet mode, from 24 to 200 |
| `--appletsrc PATH` | `~/.config/plasma-org.kde.plasma.desktop-appletsrc` | Plasma applet config to read |
| `--state-file PATH` | `~/.local/state/z13-tablet-panel/state.json` | Where the saved original height is kept |
| `--drm-root PATH` | `/sys/class/drm` | Where screen connection status is read |
| `--poll-seconds N` | 10 | Seconds between checks |
| `--dry-run` | off | Log `would set` and `would restore`; never write the panel height or the state file |
| `--status` | off | Print one check of tablet mode, screens, the panel and the state file, then exit; never writes |
| `-h`, `--help` | | Show the help and exit |

Usage errors, such as a height outside 24 to 200, exit with status 2. The service unit does not restart after status 2.

## Requirements

- PyGObject: `sudo pacman -S --needed python-gobject`
- KDE Plasma 6 with KWin on the session bus, which reports tablet mode there.

## Install

Preview the installer from the repository root. It changes nothing:

```bash
bash linux/z13-tablet-panel/install.sh --dry-run
```

Install as your normal user. The installer refuses to run as root:

```bash
bash linux/z13-tablet-panel/install.sh
```

The installer copies the helper to `~/.local/bin/z13-tablet-panel`, copies the unit to `~/.config/systemd/user/`, and enables and restarts the service. It warns, but does not stop, when PyGObject is missing.

## Check it works

Print tablet mode, the screen count, the panel id, the state file and the panel height, then what the service would do now. It never writes. The panel height is read only when the laptop is the only screen:

```bash
~/.local/bin/z13-tablet-panel --status
```

Run the helper in a terminal with `--dry-run` to log `would set` and `would restore` without changing the panel. Stop it with Ctrl+C:

```bash
~/.local/bin/z13-tablet-panel --dry-run
```

Show the service's recent log lines:

```bash
journalctl --user -u z13-tablet-panel.service -n 20
```

## Uninstall

Run this from the repository root:

```bash
bash linux/z13-tablet-panel/install.sh --uninstall
```

The uninstall stops the service first, which restores the saved height when the laptop is the only screen. It then disables the service and removes the helper and the unit file. If the state file is still there, the installer prints a warning, because the panel may still be tablet-sized.

## Tests

Run the hermetic unit tests from the package directory. They use fake D-Bus, sysfs and appletsrc data, and never touch the real session bus, plasmashell, `/sys` or your home directory:

```bash
python3 -B -m unittest -v
```

## Known limits

- KDE Plasma 6 only. The service uses plasmashell's Plasma 6 scripting API (`panelById`) and KWin's tablet-mode property.
- The service trusts Plasma's `lastScreen=0` record to name the laptop's panel. If Plasma puts the laptop's panel on another screen, the service finds no panel or changes a different one.
- Only the number of connected screens is checked. The service does not check which connector the screen is on.
- The restore waits for the laptop to be alone. If the service is stopped while a dock is attached, the saved height stays until the service runs again with the laptop alone.
- A state file that cannot be read is never overwritten or deleted. The service leaves the panel alone and logs why. Delete that file by hand to reset.
