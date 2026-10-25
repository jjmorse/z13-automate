# z13-game-profile

This package switches KDE's power profile to Performance while chosen Steam games run, but only while the machine is on AC power. It is written for an ASUS ROG Flow Z13 (GZ302EA) with TLP and `tlp-pd`; other setups are untested.

## What it does

The power profile service on the system bus lets a program take a hold on a profile. The hold lasts while that program stays connected to the bus. The helper takes a hold on `performance` while a game runs on AC power, and drops it when the game exits or the charger is unplugged. Charger-based switching keeps working underneath, so the normal profile comes back by itself when the hold ends.

The built-in games are Total War: WARHAMMER III (Steam app id 1142710) and Horizon Forbidden West (Steam app id 2420110). Add more in `~/.config/z13-game-profile/games.conf`.

## How a game is detected

Every 5 seconds the helper scans `/proc` for processes of your user. A game counts as running when a process has the arguments `SteamLaunch` and `AppId=<id>`, which Steam's `reaper` wrapper has for every game it starts. A game also counts when a process has `SteamAppId=<id>` in its environment.

## When it holds

- It holds only on AC power. A machine with no mains supply, such as a desktop, counts as on AC.
- It releases the hold 15 seconds after the last game process disappears, so a launcher handing over to the game does not make the profile flap.
- Unplugging the charger releases the hold at once.
- It logs only when its decision changes and when a problem first appears.

## Add a game

1. Find the app id in the Steam store URL (`store.steampowered.com/app/<id>/`) or in `steamapps/appmanifest_<id>.acf`.
2. Add a line to `~/.config/z13-game-profile/games.conf` with the id, then an optional name after a space, for example `1234567 My Game`. Blank lines and lines starting with `#` are ignored.
3. Restart the service:

```bash
systemctl --user restart z13-game-profile.service
```

A line with an id that is already in the built-in list replaces that game's name.

## Requirements

- PyGObject: `sudo pacman -S --needed python-gobject`
- A power profile service that provides `org.freedesktop.UPower.PowerProfiles` on the system bus: `tlp-pd` (see `linux/z13-power-profile/`) or `power-profiles-daemon`.

## Install

Preview the installer from the repository root. It changes nothing:

```bash
bash linux/z13-game-profile/install.sh --dry-run
```

Install as your normal user. The installer refuses to run as root:

```bash
bash linux/z13-game-profile/install.sh
```

The installer copies the helper to `~/.local/bin/z13-game-profile`, copies the unit to `~/.config/systemd/user/`, and enables and restarts the service. It warns, but does not stop, when PyGObject is missing or when the power profile service is not on the system bus.

## Check it works

Print the configured games, the games found running, whether the machine is on AC, the current profile and holds, and what the service would do now. It takes no hold:

```bash
~/.local/bin/z13-game-profile --status
```

Run the helper in a terminal with `--dry-run` to log `would hold` and `would release` without changing the profile. Stop it with Ctrl+C:

```bash
~/.local/bin/z13-game-profile --dry-run
```

Show the service's recent log lines:

```bash
journalctl --user -u z13-game-profile.service -n 20
```

While a game runs, show the holds the power profile service has:

```bash
busctl --system get-property org.freedesktop.UPower.PowerProfiles /org/freedesktop/UPower/PowerProfiles org.freedesktop.UPower.PowerProfiles ActiveProfileHolds
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--app-id ID` | none | Add a Steam app id; repeat for more |
| `--config PATH` | `~/.config/z13-game-profile/games.conf` | Games file to read |
| `--profile NAME` | `performance` | Profile to hold |
| `--poll-seconds N` | 5 | Seconds between scans |
| `--dry-run` | off | Log `would hold` and `would release`; never change the profile |
| `--status` | off | Print the current state once and exit; never takes a hold |

## Uninstall

Run this from the repository root:

```bash
bash linux/z13-game-profile/install.sh --uninstall
```

The uninstall stops and disables the service, and removes the helper and the unit file. Your `games.conf` is kept.

## Known limits

- Steam games only. Detection relies on Steam's `reaper` wrapper and the `SteamAppId` variable, so other launchers are not detected.
- While the hold is active, KDE's Power Mode slider shows Performance.
- On battery the helper holds nothing, so the usual charger-based profile applies.
- If the power profile service does not offer `performance`, the helper logs that once and does nothing.
