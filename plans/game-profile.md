# Plan: z13-game-profile

Branch: `game-profile` (off `main`). New package: `linux/z13-game-profile/`. Do not mention any private repository, private paths, hostnames or personal data in any file.

## Goal

On Linux with TLP and `tlp-pd` (which provides KDE's Power Mode slider), switch the power profile to Performance while chosen Steam games are running, only while on AC power, and drop back to the normal profile when the game exits or the charger is unplugged. This replaces the Windows habit of switching to Turbo by hand or with a tool. Default games: Total War: WARHAMMER III (Steam app id 1142710) and Horizon Forbidden West (Steam app id 2420110), with a config file to add more.

## Design

One Python 3 script using PyGObject (`gi.repository.Gio`, `GLib`; the other user services in this repository already depend on it), run as a long-lived user service. Script name `z13-game-profile.py`, installed as `~/.local/bin/z13-game-profile`.

### Profile switching: a hold, not a set

The power-profiles D-Bus API on the system bus (bus name `org.freedesktop.UPower.PowerProfiles`, object `/org/freedesktop/UPower/PowerProfiles`, interface `org.freedesktop.UPower.PowerProfiles`; `tlp-pd` also owns the legacy name `net.hadess.PowerProfiles` with the same methods) has `HoldProfile(profile: s, reason: s, application_id: s) -> cookie: u` and `ReleaseProfile(cookie: u)`. A hold lasts only while the caller stays connected to the bus, and the daemon releases it automatically when the caller exits, so the script must keep one `Gio.DBusConnection` open for its whole life and never use a one-shot tool like `busctl call` for the hold. Properties: `ActiveProfile` (s), `Profiles` (aa{sv}, each with `Profile`), `ActiveProfileHolds` (aa{sv}, entries normally carry `ApplicationId`, `Profile` and `Reason`). Use the hold so the existing charger-driven profile switching (done by PowerDevil: balanced on AC, power-saver on battery) keeps working underneath and returns by itself when the hold ends. Hold the profile named `performance` with application id `z13-game-profile` and a reason like `Total War: WARHAMMER III is running`. If `performance` is not listed in `Profiles`, log that once and do nothing.

### Game detection

Poll every 5 seconds (option `--poll-seconds`). A Steam game is running when any process of the same user has either of these:

1. A command line (from `/proc/<pid>/cmdline`, NUL-separated) that contains the argument `SteamLaunch` and an argument exactly `AppId=<id>`. Steam wraps each launched game in a `reaper` process whose command line looks like: `/home/user/.local/share/Steam/ubuntu12_32/reaper SteamLaunch AppId=1142710 -- /home/user/.local/share/Steam/steamapps/common/SteamLinuxRuntime_sniper/_v2-entry-point --verb=waitforexitandrun -- /home/user/.local/share/Steam/compatibilitytools.d/proton/proton waitforexitandrun /path/to/Warhammer3.exe`.
2. An environment (from `/proc/<pid>/environ`, NUL-separated) containing `SteamAppId=<id>`.

Read `/proc` with `os.scandir`, ignore processes that vanish or cannot be read, and read at most 64 KiB per file. Check the command line of every process first and read `environ` only for processes whose command line did not already match, and only for processes whose `comm` is `reaper`, `wine`, `wineserver`, `wine64`, `proton`, or ends in `.exe`, to keep the scan cheap. The whole scan must stay well under 50 ms on a desktop.

### AC only

On AC means at least one directory in `/sys/class/power_supply/` has `type` containing `Mains` and `online` equal to `1`. On this machine the supply is called `AC0`, but do not hard-code the name. If none is online the machine is on battery. If no Mains supply exists at all (a desktop), treat it as on AC.

### State machine

Each poll computes `want = on_ac and any configured game is running`. If `want` and no hold is active, take the hold. If not `want` and a hold is active, release it, but only after `want` has been false for 15 consecutive seconds (a debounce so a launcher handing over to the game, or a brief crash and restart, does not flap the profile); unplugging the charger releases immediately, with no debounce. While holding, verify each poll that the daemon still lists our hold in `ActiveProfileHolds` (match on `ApplicationId` equal to `z13-game-profile`; if the property cannot be read or has an unexpected shape, assume the hold is still there); if it is missing (for example `tlp-pd` restarted), call `HoldProfile` again. If the daemon is not on the bus, log it once and keep polling; do not exit. A failed call is logged once per distinct error and retried at the next poll. On service shutdown (SIGTERM or SIGINT) release the hold and exit cleanly with status 0.

### Games list and config

Built-in default list: `1142710` named `Total War: WARHAMMER III` and `2420110` named `Horizon Forbidden West`. An optional config file `~/.config/z13-game-profile/games.conf` (path overridable with `--config PATH`) adds games, one per line: an app id, then optional whitespace and a name; blank lines and lines starting with `#` are ignored; malformed lines are skipped with a log line. Games from the file are added to the defaults (the same id in the file replaces the default's name). Option `--app-id ID` (repeatable) adds ids on the command line too.

### Logging

Print one line to stdout (journald) whenever the decision changes (hold taken with the game name, hold released with the reason: game exited, charger unplugged, or service stopping) and when a problem appears for the first time. Never log every poll.

### Command line

`z13-game-profile [--app-id ID ...] [--config PATH] [--profile NAME] [--poll-seconds N] [--proc-root PATH] [--power-supply-root PATH] [--dry-run] [--status] [-h]`. Defaults: profile `performance`, poll 5, `/proc`, `/sys/class/power_supply` (the two root options exist so tests can use fake trees). `--dry-run` logs "would hold" and "would release" but never calls HoldProfile or ReleaseProfile. `--status` runs one detection pass and prints: the configured games, the games found running (name and pid), whether the machine is on AC, the daemon's active profile and current holds (or "daemon not on the bus"), and what the service would do now; it never takes a hold and exits 0. Exit status 2 for usage errors.

## Files

- `linux/z13-game-profile/z13-game-profile.py` (executable)
- `linux/z13-game-profile/test_z13_game_profile.py` (unittest, hermetic)
- `linux/z13-game-profile/z13-game-profile.service`
- `linux/z13-game-profile/install.sh`
- `linux/z13-game-profile/README.md`
- `README.md` (top level): add a feature bullet next to the others near line 15 and a table row next to the `linux/z13-power-profile/` row near line 129, in the same style.

House style: use `linux/z13-claude-idle-sleep/` on this branch as the model for the service, `install.sh` (flags `--dry-run`, `--uninstall`, `-h|--help`; refuses to run as root; `run` helper that prints each command; the installer ends with `systemctl --user enable` then `systemctl --user restart` so a reinstall takes effect), tests (hermetic `unittest`, the script loaded by path with `importlib`) and README. The unit file: `Description=Hold the Performance power profile while chosen Steam games run on AC`, `ExecStart=%h/.local/bin/z13-game-profile`, `After=graphical-session.target`, `PartOf=graphical-session.target`, `Restart=on-failure`, `RestartSec=5`, `RestartPreventExitStatus=2`. `install.sh` warns (but does not fail) when `python3 -c 'from gi.repository import Gio'` fails (hint: `sudo pacman -S --needed python-gobject`) and when the power-profiles service `org.freedesktop.UPower.PowerProfiles` is not on the system bus (hint: it comes from `tlp-pd`; see the `linux/z13-power-profile/` package). The README covers: what it does and why a hold (it returns by itself, and coexists with charger-based switching), how a game is detected, AC only, the 15 s release delay, adding games (find the app id in the Steam store URL or `steamapps/appmanifest_<id>.acf`, then add a line to `games.conf` and `systemctl --user restart z13-game-profile.service`), checking (`--status`, `journalctl --user -u z13-game-profile.service`, and in a terminal while a game runs `busctl --system get-property org.freedesktop.UPower.PowerProfiles /org/freedesktop/UPower/PowerProfiles org.freedesktop.UPower.PowerProfiles ActiveProfileHolds`), install, uninstall, and limits (needs `tlp-pd` or `power-profiles-daemon`; Steam games only; relies on Steam's `reaper` wrapper and the `SteamAppId` variable; the profile shown in the KDE slider changes while the hold is active).

## Tests (all hermetic: no real D-Bus, no real `/proc` or `/sys`, no sleeping, no files outside a temp dir)

Use a temporary fake `/proc` tree (directories named by pid with `cmdline`, `environ` and `comm` files) and a fake power-supply tree, an injected fake D-Bus client class that records `HoldProfile` and `ReleaseProfile` calls and serves `ActiveProfileHolds`, `ActiveProfile` and `Profiles`, and an injected fake clock and scheduler. Cover: cmdline match on the reaper example above; no match for a similar id (`AppId=11427100`, `AppId=114271`); environ match; unreadable or vanishing pid; ignored non-matching comm for environ; the config parser (valid, comments, blank, malformed lines, duplicate ids, command-line ids); AC detection (Mains online, Mains offline, only Battery type, no supplies at all, a USB type supply that must not count); the state machine: hold taken on game plus AC, no hold when on battery, charger unplugged releases immediately while a game runs, charger returning takes the hold again, game exit releases only after 15 s of consecutive absence, a game restarting inside the 15 s keeps the same hold, hold missing from `ActiveProfileHolds` is re-taken, an unreadable or oddly shaped property is treated as still held, daemon not on the bus is logged once and polling continues, a failed call is logged once per distinct error, `performance` not listed in `Profiles` does nothing and logs once, `--dry-run` never calls hold or release, shutdown releases the hold, `--status` never takes a hold and prints the expected fields, exit status 2 on a bad option. Run with `python3 -B -m unittest -v` from the package directory.

## Validation after the build (done by the orchestrator, not the builders)

Run the unit tests; run `--status` against the real `/proc`, `/sys` and system bus (no game running, so it should report none found, AC online, balanced); run `bash -n` on `install.sh` and `install.sh --dry-run`. Then give the user the commands to install and a real test: install, start a configured game, check `--status` and the `ActiveProfileHolds` property, quit the game, wait 20 s and check again, plus a charger-unplug check. The user starts any timed or physical test. Rollback: `bash linux/z13-game-profile/install.sh --uninstall`.
