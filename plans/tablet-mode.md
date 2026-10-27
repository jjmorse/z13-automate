# Plan: tablet mode on the Z13 (Plasma Mobile session and an in-session tablet-mode watcher)

Branch: `tablet-mode` (off `main`). Do not mention any private repository, private paths, hostnames or personal data in any file.

## Goal

Make the Z13 comfortable with the keyboard folio detached. Two tracks, both requested: (1) try the Plasma Mobile shell as a separate login session; (2) an in-session watcher that applies touch-friendly settings when KWin reports tablet mode and reverts them when the keyboard is reattached, with no logout. Plus a fix for the on-screen keyboard not appearing in some text fields.

## Findings so far

- KWin exposes tablet mode on the session bus: service `org.kde.KWin`, object `/org/kde/KWin`, interface `org.kde.KWin.TabletModeManager`, properties `tabletMode` and `tabletModeAvailable`, signals `tabletModeChanged(b)` and `tabletModeAvailableChanged(b)`. `tabletModeAvailable` is true on the Z13; `tabletMode` is false while the folio is attached.
- `plasma-mobile` and `plasma-settings` are in the Arch `extra` repository at the same version as the desktop (6.8.1). Mobile installs as a separate login-screen session; a session cannot be swapped live, so changing shells means logging out.
- Electron and Chromium apps run through XWayland unless told otherwise, so the Wayland on-screen keyboard (Plasma Keyboard, text-input-v3) never receives their focus events. The ChatGPT desktop launcher reads `~/.config/chatgpt-flags.conf`; Claude Desktop is a bare Electron binary with no flags-file support.

## Track A: on-screen keyboard in Electron apps

1. ChatGPT: `~/.config/chatgpt-flags.conf` with `--ozone-platform=wayland`, `--enable-wayland-ime`, `--wayland-text-input-version=3` (done; needs an app restart to take effect).
2. Claude Desktop: no flags file. Options are a `~/.local/share/applications/com.anthropic.Claude.desktop` `Exec=` override carrying the same flags, or a small wrapper. Needs Josh's approval and a full quit of Claude first.
3. Document the Electron flags in the `linux/z13-onscreen-keyboard/` README.

## Track B: Plasma Mobile session

1. Install `plasma-mobile` and `plasma-settings` (needs `sudo`; Josh runs it).
2. Log out, pick the Plasma Mobile session at the login screen, test undocked. Stop `z13-display-restore.service` first if the dock is attached.
3. Undo: `sudo pacman -Rns plasma-mobile plasma-settings`.

## Track C: in-session tablet-mode watcher

New package `linux/z13-tablet-mode/`, following the house style of `linux/z13-game-profile/` (one Python 3 script using PyGObject, a user service, `install.sh` with `--dry-run`, `--uninstall`, `-h|--help`, hermetic `unittest` tests, a README).

1. Discovery first (read-only): snapshot panel geometry, KWin settings, screen rotation, virtual keyboard mode and other candidate state with the keyboard attached and again detached, then diff, so only settings that actually change live are used.
2. Subscribe to `tabletModeChanged` and `tabletModeAvailableChanged`; on each change apply the chosen "tablet" settings, on the opposite change revert to the saved "desktop" values. Re-apply on login and when KWin restarts.
3. The chosen settings are a decision for Josh after the diff in step 1. Candidates to verify live: panel thickness, window maximize behaviour, screen-edge touch gestures, auto-rotate, launcher style.
4. No logout, no package changes, and no change to the dock or display layout code. `z13-vkeyboard-follow` (unmerged branch `vkeyboard-follow`) already drives the keyboard mode from the same signal; keep the two independent.

## Validation

Unit tests with a fake D-Bus client and fake clock; `--dry-run` run against the real session bus; then Josh detaches and reattaches the folio and checks each behaviour. Rollback: `bash linux/z13-tablet-mode/install.sh --uninstall`.
