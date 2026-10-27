# Plan: tablet mode on the Z13 (Plasma Mobile session, swipe-up keyboard, touch taskbar)

Branch: `tablet-mode` (off `main`). Do not mention any private repository, private paths, hostnames or personal data in any file.

## Goal

Make the Z13 comfortable with the keyboard folio detached. Tracks: (A) on-screen keyboard in Electron apps; (B) try the Plasma Mobile shell as a separate login session; (C) a swipe-up-from-the-bottom-edge gesture that shows the on-screen keyboard; (D) a larger taskbar for touch.

## Findings

- KWin exposes tablet mode on the session bus: service `org.kde.KWin`, object `/org/kde/KWin`, interface `org.kde.KWin.TabletModeManager`, properties `tabletMode` and `tabletModeAvailable`, signals `tabletModeChanged(b)` and `tabletModeAvailableChanged(b)`. `tabletModeAvailable` is true on the Z13; `tabletMode` is false while the folio is attached.
- Snapshot diff of the keyboard attached versus detached: only `tabletMode` and KWin's virtual keyboard `mode` change (the latter by `z13-vkeyboard-follow`). Panel layout, `kwinrc`, `kdeglobals`, screen scale and rotation do not change on their own; auto-rotate already follows tablet mode.
- `plasma-mobile` and `plasma-settings` are in the Arch `extra` repository at the same version as the desktop (6.8.1). Mobile installs as a separate login-screen session (`plasma-mobile.desktop`, `startplasmamobile`); a session cannot be swapped live, so changing shells means logging out.
- Electron and Chromium apps run through XWayland unless told otherwise, so the Wayland on-screen keyboard (Plasma Keyboard, text-input-v3) never receives their focus events. The ChatGPT desktop launcher reads `~/.config/chatgpt-flags.conf`; Claude Desktop is a bare Electron binary with no flags-file support.
- KDE has no built-in screen-edge action for the keyboard (a 2018 request was closed as intentional), but KWin 6.8.1 scripts have `registerTouchScreenEdge` and `callDBus`, and KWin's `/VirtualKeyboard` object (`org.kde.kwin.VirtualKeyboard`) has a `forceActivate` method. Reading the touchscreen directly would need the `input` group, which also allows keylogging, so it is not used.
- Reading panels through plasmashell's `evaluateScript` D-Bus method (`panels()` and its properties) segfaults plasmashell on 6.8.1, even for `id`, `location` and `height` only (stack: a panel property getter calling `KConfigGroup::readEntry`). Live panel resizing by scripting is therefore out. The panel height is stored as `thickness` under `[PlasmaViews][Panel N][Defaults]` in `plasmashellrc`.

## Track A: on-screen keyboard in Electron apps

1. ChatGPT: `~/.config/chatgpt-flags.conf` with `--ozone-platform=wayland`, `--enable-wayland-ime`, `--wayland-text-input-version=3` (done; needs an app restart to take effect).
2. Claude Desktop: no flags file. Option: a `~/.local/share/applications/com.anthropic.Claude.desktop` `Exec=` override carrying the same flags. Needs Josh's approval and a full quit of Claude first.
3. Document the Electron flags in the `linux/z13-onscreen-keyboard/` README.

## Track B: Plasma Mobile session

1. `plasma-mobile`, `plasma-settings`, `plasma-nano` and `qt6-sensors` installed (done).
2. Log out, pick the Plasma Mobile session at the login screen, test undocked. Stop `z13-display-restore.service` first if the dock is attached.
3. Undo: `sudo pacman -Rns plasma-mobile plasma-settings plasma-nano`.

## Track C: swipe-up keyboard (KWin script)

New package `linux/z13-swipe-keyboard/`: a KWin script (KPackage `KWin/Script`, JavaScript) that registers a touch screen edge on the bottom edge with `registerTouchScreenEdge` and, on the swipe, calls `forceActivate` on `/VirtualKeyboard` through `callDBus`. User-level install with `kpackagetool6`, enabled through `kwriteconfig6` in `kwinrc` `[Plugins]`, then reloaded. `install.sh` with `--dry-run`, `--uninstall`, `-h|--help`; refuses to run as root; hermetic tests; README. No input-group access, no extra packages. The edge constant name, and whether `forceActivate` shows the keyboard while the mode is 0, are verified on the real touchscreen by Josh.

## Track D: touch-friendly taskbar

Live resize is not possible (see findings). Options: (1) make the Z13's own panel taller once, in Plasma Edit Mode; (2) a watcher that edits `thickness` in `plasmashellrc` and restarts plasmashell on every detach and reattach (flickers the panel and reloads widgets). Josh chooses.

## Validation

Unit tests are hermetic (no real KWin, no `kwriteconfig6` against the real config). Then the orchestrator installs and loads the script, and Josh checks the swipe on the touchscreen. Rollback: `bash linux/z13-swipe-keyboard/install.sh --uninstall`.
