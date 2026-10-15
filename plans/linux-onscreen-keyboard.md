# Linux on-screen keyboard that follows the keyboard folio

## Problem

KDE Plasma's own virtual keyboard (Plasma Keyboard) pops up on touch and tablet input but has no Tab, Ctrl or arrow keys. Onboard has the full layout and can show itself when an editable text field is focused, but it shows itself even when the folio keyboard is attached, and it needs a few settings to work on Wayland.

## Fix

- Onboard runs under XWayland (`GDK_BACKEND=x11`), with GTK input events and uinput for keystrokes, and desktop accessibility turned on so apps report their text fields to it.
- A small user service watches KWin's tablet-mode flag (`org.kde.KWin.TabletModeManager`, which turns on when the folio is detached) and sets Onboard's `AutoShowPaused` property on D-Bus: paused and hidden with the keyboard attached, active in tablet mode.
- An installer with `--dry-run` and `--uninstall`, and a README with the settings, the checks and the limits.

## Steps

1. Branch `linux-onscreen-keyboard` from main (done).
2. A subagent writes `linux/z13-onscreen-keyboard/` (watcher script, two user units, installer, README); check it with `bash -n`, `python3 -m py_compile`, dry runs and a fake-D-Bus test.
3. Install it on the Z13 and test with the folio attached and detached.
4. Add the main README entries; commit; merge only with Josh's approval.

## Known limits

- Apps started before accessibility was turned on do not report their text fields until restarted. The setting is permanent after the first login.
- The Claude desktop app and other Chromium or Electron apps may need their own accessibility switch; untested.
- Onboard on Wayland is run through XWayland, which is a workaround, not a supported setup.
