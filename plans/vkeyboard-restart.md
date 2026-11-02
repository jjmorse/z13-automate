# Plan: restart the on-screen keyboard when it is likely to be stale

Branch: `vkeyboard-follow` (the existing branch of the follower this extends). Do not mention any private repository, private paths, hostnames or personal data in any file.

## Problem

After about two days of uptime with many suspends and dock changes, Plasma Keyboard stopped appearing in every app (launcher search, browsers, Electron apps) until its process was restarted. The cause is unknown; a fresh process fixed it. KWin's log line "Input method is trying to show itself but without a context" appears dozens of times a day in normal use, so it is not a reliable failure signal.

## Findings

- KWin starts the input method (`InputMethod` in `kwinrc` group `Wayland`; here `swipe-keyboard launch`, which becomes a `plasma-keyboard` process, a direct child of `kwin_wayland`) and restarts it only after a crash exit, at most 5 times within 20 seconds, then logs "Input Method keeps crashing" and stops it.
- A plain `kill` (SIGTERM) makes the process exit normally and KWin does not relaunch it. `kill -9` (SIGKILL) is a crash exit: verified live, KWin logs "Input Method crashed" and the new process is running within 1 second.
- A same-value or un-notified `InputMethod` config write does nothing; two notified changes (`kwriteconfig6 --notify`) to a different desktop file and back do relaunch it. This is the fallback, not used by the service.

## Design

Extend `linux/z13-vkeyboard-follow/` (a user service that already follows tablet mode over D-Bus):

1. Restart the keyboard process when tablet mode turns on (a change observed from off to on, never at service start) and when the system resumes from sleep (logind `PrepareForSleep(false)` on the system bus) while tablet mode is on.
2. Restart means SIGKILL to the keyboard process. The process is found by scanning `/proc`: the name matches (default `plasma-keyboard`), the parent is KWin's pid (from `GetConnectionUnixProcessID` on the bus daemon) and the real uid is the service's uid. Nothing else is ever signalled.
3. At most one restart per 30 seconds (KWin gives up after 5 crashes in 20 seconds).
4. About 3 seconds after a restart, check that a process with a different pid is running and log the result.
5. `Z13_VKEYBOARD_RESTART=0` turns restarts off; `Z13_VKEYBOARD_PROCESS` changes the process name.
6. Every restart is logged with its reason, so the pattern of wedges can be studied later.

## Validation

Hermetic unit tests (fake `/proc` tree, fake bus, fake clock and scheduler; nothing touches the real bus or processes). Then the orchestrator installs the service, restarts it, and checks a live restart on detach while Josh is in tablet mode. Rollback: `bash linux/z13-vkeyboard-follow/install.sh --uninstall`, or set `Z13_VKEYBOARD_RESTART=0` in a drop-in.
