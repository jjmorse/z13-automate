# Fix: brightness/refresh not applied on power-source change (make the tray own it)

## Problem
On plugging in AC, the display sometimes stays at the DC profile (e.g. brightness stuck at 40% instead of 90%). Refresh may or may not switch. Recurring ("once again").

## Root cause (evidenced 2026-09-21)
Automatic profile switching depends entirely on the **"Display Power Profile" scheduled task** firing on **Kernel-Power Event 105** and running `Apply-DisplayPower.ps1` -> `Invoke-Z13AutoProfile`. That path is fragile:
- The task's last run (17:54:52) returned **0x41306 = SCHED_S_TASK_TERMINATED** -- it fired on plug-in but was killed mid-run (overlap/time-limit) before finishing its ~7s settle delay and setting brightness. So no "apply 180Hz/90% (auto/AC)" line was ever logged and brightness stayed at the DC value.
- `z13-automate.log` confirms: the last entry is the DC apply; no AC apply followed the plug-in.
- The tray app (`Z13Tray.ps1`) is passive: its 5s timer only READS state for the icon/tooltip; it never applies a profile except on manual menu clicks.

The apply logic itself is fine (`Set-DisplayBrightness` logs "brightness -> N%" reliably when it runs). The failure is the trigger.

## Fix design
Make the always-running tray the reliable responder to power changes; keep the scheduled task as a harmless backup (apply is idempotent).

Changes in `Z13Tray.ps1` only (do NOT change `Z13Display.psm1` apply logic):
1. Add a guarded helper `Start-ApplyProfile` that launches `Apply-DisplayPower.ps1` as a **hidden background process** (`powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File Apply-DisplayPower.ps1`). Running it as a separate process reuses the tested apply + settle-delay logic and avoids freezing the tray UI thread (which the ~7s of Start-Sleep in Invoke-Z13AutoProfile would otherwise do). Guard: skip if AutomationEnabled is false, and skip if a previous apply process is still running (track `$script:applyProc`) so events cannot stack.
2. Subscribe to `[Microsoft.Win32.SystemEvents]::PowerModeChanged`; when `e.Mode -eq [Microsoft.Win32.PowerModes]::StatusChange` (AC/DC change), call `Start-ApplyProfile`. This is an in-process OS notification -- far more reliable than the event-triggered task.
3. Safety net in the existing 5s timer: track `$script:lastPowerSource`; each tick, read `Get-PowerSource`; if it changed since last tick and AutomationEnabled, call `Start-ApplyProfile`. Catches any missed PowerModeChanged within 5s.
4. Log tray-initiated applies via `Write-Z13Log` (e.g. "tray: power AC->DC, launching apply") so future diagnosis is easy.
5. Cleanup on Exit: unregister the PowerModeChanged handler (SystemEvents handlers can otherwise leak / keep the process alive).
6. Leave the menu, presets, icon, single-instance mutex behavior intact.

## Constraints
- **Pure ASCII in all .ps1 files** -- PowerShell 5.1 reads BOM-less .ps1 as ANSI; em-dashes/curly quotes/Unicode break string literals (module already warns about this). Use `->` not arrows, straight quotes only.
- Work in the LOCAL repo `C:\Users\jjmorse\Documents\Claude\Z13 Automate` (authoritative; the running tray launches from here). Ignore the OneDrive copy.
- Branch `fix-power-profile-reconcile` (already created). Never commit to main; no merge/push without approval.
- Do NOT kill or restart the running tray (PID 43544) -- the orchestrator will restart it for testing with the user's consent.

## Testing (orchestrator does the runtime test with the user)
- Static: confirm the modified `Z13Tray.ps1` parses (`[System.Management.Automation.PSParser]::Tokenize` or `powershell -NoProfile -Command "& { . script }"` guard) without launching the UI.
- Runtime (needs tray restart + power toggle -> user consent): restart tray, then `Apply-DisplayPower.ps1 -Force DC` / `-Force AC` and real unplug/replug; confirm brightness follows and the tray logs the transition.

## Rollback
Single-file change; revert `Z13Tray.ps1` on the branch. Scheduled task untouched, so behavior falls back to current if reverted.
