# Plan: z13-claude-idle-sleep

Branch: `claude-idle-sleep` (off `main`). New package: `linux/z13-claude-idle-sleep/`. Do not mention any private repository, private paths, hostnames or personal data in any file.

## Problem

Claude Desktop for Linux takes a five-minute `remote_tools` keep-awake claim (Electron `prevent-app-suspension`, shown by KDE as a `sleep` inhibition from `/usr/bin/claude-desktop`) about every 30 minutes, even when nothing is being done. The claim comes from the app's credential rotation: the server answers the in-band credential refresh with a `get_device_info` tool call, and the keep-awake manager counts it as a tool call. PowerDevil (Plasma 6.8) re-registers its idle timers and simulates user activity on every inhibition change, and its suspend action simply returns when sleep is inhibited, so a 60-minute idle-sleep timeout never fires while Claude Desktop is open and Remote Control is on. The longest claim-free stretch is only about 22 to 27 minutes. Related public report: https://claudeissues.com/issue/89110-bug-claude-desktop-inhibits-sleep-in-linux-and-leaves-orphaned-processes-when-qu

## Goal

A small per-user systemd service that suspends the machine after N seconds (default 3600) of no keyboard or mouse input, but only when the sole thing blocking sleep is Claude Desktop and Claude's own log shows nothing but the rotation claims for that whole period. Any real activity (Code turn, manual hold, remote tool call, any unrecognised keep-awake log line) postpones sleep. Anything unexpected must fail safe, meaning do not suspend. If the helper is disabled or Claude is closed, nothing changes.

## Design

One Python 3 script using only the standard library, run as a long-lived user service. Name: `z13-claude-idle-sleep.py`, installed as `~/.local/bin/z13-claude-idle-sleep`.

### Input idle

`GetSessionIdleTime` is not supported on KDE Wayland, so idle comes from `swayidle` (Arch `extra`, 36 KiB, speaks ext-idle-notify-v1, which KWin 6.8 offers). The script spawns `swayidle timeout <idle_seconds> 'echo IDLE' resume 'echo RESUME'` with stdout piped and reads lines: `IDLE` sets idle true, `RESUME` sets it false. Do not pass `-w`. If `swayidle` is not installed, print a clear message including `sudo pacman -S --needed swayidle` and exit with status 2 (the unit has `RestartPreventExitStatus=2`). If `swayidle` exits, exit with status 1 so systemd restarts the service.

### Main loop

Use `selectors` on the swayidle pipe with a timeout of `poll_seconds` (default 60). After each event or timeout, run one evaluation. Keep state: `idle` (bool), `grace_until` (epoch), `last_boot_delta`.

### Resume grace

Each tick compute `delta = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()`. If `delta` grew by more than 5 seconds since the previous tick, the machine slept: set `grace_until = now + 600`. This stops an immediate re-suspend after a wake that was not caused by input.

### Inhibitions

Read KDE's active inhibitions with `busctl --user --json=short get-property org.kde.Solid.PowerManagement.PolicyAgent /org/kde/Solid/PowerManagement/PolicyAgent org.kde.Solid.PowerManagement.PolicyAgent ActiveInhibitions`. The output looks like `{"type":"a(ssssu)","data":[["idle","Steam","Game streaming (server)","block",3],["sleep","/usr/bin/claude-desktop","Electron","block",3]]}` where each row is `[what, app, reason, mode, flags]`. Run it with `subprocess.run([...], capture_output=True, text=True, timeout=5)`; on any error, non-zero status or unparsable output the result is "unknown", which means do not suspend. Rule: if the list is empty, do nothing (PowerDevil handles it). If any row's app is not exactly `/usr/bin/claude-desktop`, do nothing. Only when every row is Claude's does the log analysis matter. This is deliberately conservative: other apps' `idle` inhibitions (games, video) also block the helper.

### Claude log analysis

Read the last 2 MiB of `~/.config/Claude/logs/main.log` (option `--log PATH`). Lines look like `2026-10-21 20:01:48 [info] <message>` in local time. Real examples of the rotation-only pattern (all within one or two seconds of each other, then the release exactly five minutes later):

```
2026-10-21 20:01:48 [info] [remote-tools-device] rotation due — re-presenting credential in-band
2026-10-21 20:01:48 [info] [keep-awake] started (id=78, claims=remote_tools)
2026-10-21 20:01:48 [info] [remote-tools-device] keep-awake held (pss=0)
2026-10-21 20:01:48 [info] [remote-tools-device] served get_device_info
2026-10-21 20:01:48 [info] [remote-tools-device] credential re-verified in-band
2026-10-21 20:06:48 [info] [keep-awake] stopped (id=78): last claim remote_tools released
2026-10-21 20:06:48 [info] [remote-tools-device] keep-awake released (quiet, held 300001ms)
```

Real examples of genuine activity (a Code turn):

```
2026-10-21 22:09:02 [info] [keep-awake] started (id=83, claims=code_turn)
2026-10-21 22:09:02 [info] [keep-awake] code session hold taken (turn, battery=false)
2026-10-21 22:12:40 [info] [keep-awake] code session hold released (idle, held 218000ms, longest stretch without input 5000ms)
2026-10-21 22:12:40 [info] [keep-awake] stopped (id=83): last claim code_turn released
```

Other genuine-activity lines include `[keep-awake] session <id> manual hold taken`, `... manual hold released`, `[keep-awake] session <id> no longer counted`, and any other line containing `[keep-awake]`.

Classification rules:

1. A rotation interval is: a line `[remote-tools-device] keep-awake held` at time H, with a line `[remote-tools-device] rotation due — re-presenting credential in-band` at a time in [H-2s, H], followed by the next `[remote-tools-device] keep-awake released (quiet, held N ms)` at time R, where 299000 <= N <= 302000. A hold that lasted longer than that was extended by a real tool call, so it is NOT a rotation interval.
2. A rotation interval with no release line yet is "ongoing" and still counts as rotation-only while `now - H <= 330` seconds. Past that it counts as real activity.
3. Lines that belong to a rotation interval are ignored: the rotation-due line, `served get_device_info`, `credential re-verified in-band`, the two `[remote-tools-device] keep-awake held/released` lines, and the `[keep-awake] started (id=N, claims=remote_tools)` line within 2 seconds of H and the `[keep-awake] stopped (id=N): last claim remote_tools released` line within 2 seconds of R.
4. Every other line containing `[keep-awake]` or `[remote-tools-device] keep-awake`, and every `[remote-tools-device] served <tool>` line where `<tool>` is not `get_device_info`, is real activity at that line's timestamp. `last_real` is the latest such timestamp.
5. Track open holds: `[keep-awake] started (id=N, claims=...)` opens id N and `[keep-awake] stopped (id=N)` closes it; `code session hold taken/released` and `session <id> manual hold taken/released` open and close likewise. An id opened by a rotation interval does not count. Any other hold still open at the end means "real hold in progress": do not suspend. An unmatched `stopped` (log tail started mid-way) is ignored.
6. History check: if the earliest parsed timestamp in the data read is newer than `now - idle_seconds` and the whole file was not read (the file is larger than the 2 MiB window), the history is too short: do not suspend. A missing or unreadable log also means do not suspend. A log with no activity lines at all but enough history means no recent activity.
7. If a timestamp cannot be parsed, skip that line; if a line mentions `[keep-awake]` but its shape is not recognised, treat it as activity at the previous parsed timestamp (or now if none).

### Decision

A pure function `decide(now, idle, grace_until, inhibitions, activity, idle_seconds)` returning `(suspend: bool, reason: str)`, checked in this order, with these reason strings:

1. not idle: `not idle`
2. `now < grace_until`: `grace period after resume`
3. inhibitions unknown: `inhibitions unreadable`
4. inhibitions empty: `no inhibitions (PowerDevil handles sleep)`
5. any non-Claude row: `blocked by <app>`
6. history too short or log unreadable: `Claude log history too short`
7. real hold in progress: `Claude hold in progress: <claims>`
8. `now - last_real < idle_seconds`: `Claude activity <N>s ago`
9. otherwise: `suspend` (reason `only Claude rotation claims for <idle_seconds>s`)

### Acting

On `suspend`: log it to stdout (journald) and call `busctl --user call org.kde.Solid.PowerManagement /org/freedesktop/PowerManagement org.freedesktop.PowerManagement Suspend` (PowerDevil's own suspend, so KDE's lock-on-resume behaviour is kept). With `--dry-run`, log `would suspend` instead and do not call it. Log a line only when the reason changes, plus a line for every suspend, so the journal stays quiet.

### Command line

`z13-claude-idle-sleep [--idle-seconds N] [--poll-seconds N] [--log PATH] [--dry-run] [--explain] [-h]`. Defaults 3600, 60, `~/.config/Claude/logs/main.log`. `--explain` runs one evaluation assuming idle is true and the grace period is over, prints the inputs it saw (inhibition list apps, last real activity age, open holds, history ok) and the decision, never calls swayidle or suspend, and exits 0. Exit status 2 for usage errors and for missing swayidle.

## Files

- `linux/z13-claude-idle-sleep/z13-claude-idle-sleep.py` (executable)
- `linux/z13-claude-idle-sleep/test_z13_claude_idle_sleep.py` (unittest, hermetic)
- `linux/z13-claude-idle-sleep/z13-claude-idle-sleep.service`
- `linux/z13-claude-idle-sleep/install.sh`
- `linux/z13-claude-idle-sleep/README.md`
- `README.md` (top level): add a feature bullet next to the others near line 15 and a table row next to the `linux/z13-power-profile/` row near line 129, in the same style.

Use `git show vkeyboard-follow:linux/z13-vkeyboard-follow/<file>` for the house style of the service, `install.sh` (flags `--dry-run`, `--uninstall`, `-h`; refuses to run as root; `run` helper that prints each command), tests (hermetic, `unittest`, loads the script by path) and README. The unit file is the same shape as that package's, with `Description=Suspend after idle when only Claude Desktop's rotation claims block sleep`, `ExecStart=%h/.local/bin/z13-claude-idle-sleep`, `After=graphical-session.target`, `PartOf=graphical-session.target`, `Restart=on-failure`, `RestartSec=5`, `RestartPreventExitStatus=2`. `install.sh` checks `command -v swayidle` (error with the pacman hint, but only a warning under `--dry-run`), installs the script to `~/.local/bin/z13-claude-idle-sleep` mode 755 and the unit to `~/.config/systemd/user/`, reloads and enables `--now`. `--uninstall` disables the unit and removes both files. The README covers: the problem and the public issue link, what the helper does and does not do, how it decides (the rules above in plain words), the swayidle dependency, install, how to check (`--explain`, `--dry-run`, `journalctl --user -u z13-claude-idle-sleep.service`), uninstall, and limits (depends on Claude's log wording, so an app update that changes it makes the helper do nothing; other apps' inhibitions also block it; it does not touch Claude, PowerDevil or any setting).

## Tests (all hermetic: no real D-Bus, no subprocess, no sleep, no files outside a temp dir)

Cover: rotation-only log gives no real activity; a Code turn gives activity and an open hold while running; a rotation hold that lasted 301 s versus 420 s; ongoing rotation under and over 330 s; manual hold lines; an unrecognised `[keep-awake]` line counts as activity; log tail starting mid-way; short history gives history_ok false; missing log; every `decide` branch and the order of the checks; inhibition JSON parsing (Claude only, mixed, empty, malformed); resume-grace detection from fake clock deltas; `--dry-run` never invokes the suspend command and a real decision invokes it exactly once with the expected arguments (mock `subprocess.run`); swayidle-missing exit status 2; main loop with a fake swayidle stream and fake clock (idle then resume cancels). Run with `python3 -m unittest -v` from the package directory.

## Validation after the build (done by the orchestrator, not the builders)

Run the unit tests; run `--explain` against the real log and the real session bus; run `bash -n` and `shellcheck` on `install.sh`; run `install.sh --dry-run`. Then give the user the commands: `sudo pacman -S --needed swayidle`, then `bash linux/z13-claude-idle-sleep/install.sh`, then a timed test with `--idle-seconds 300 --dry-run` first. The user starts any timed test. Rollback: `bash linux/z13-claude-idle-sleep/install.sh --uninstall`.
