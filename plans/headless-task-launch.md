# Launch background tasks headless (no Windows Terminal window at logon)

## Problem

At sign-in a Windows Terminal window stays open on the desktop, titled `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`. It hosts the **EmuDeck Hash Sync Watcher** (`Watch-ESDESessions.ps1`), so closing it kills the watcher.

## Cause (reproduced 2026-10-07)

The scheduled tasks run `powershell.exe -WindowStyle Hidden ...`. Task Scheduler cannot start a process with a hidden window, so PowerShell creates its console first and hides it afterwards. With Windows Terminal as the default terminal ("Let Windows decide" on Windows 11), the console is handed to Windows Terminal, which opens its own window. PowerShell hides only the console it knows about, not the Terminal window.

A/B test with two throwaway tasks that slept 15 s: the `-WindowStyle Hidden` launch opened a new `CASCADIA_HOSTING_WINDOW_CLASS` window; `conhost.exe --headless powershell.exe ...` ran the same command with zero new windows.

## Fix

Launch each interactive-session task through `%SystemRoot%\System32\conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File "..."`. `--headless` runs the console app with no window and no terminal hand-off, and the default terminal setting stays untouched. `-WindowStyle Hidden` is dropped because nothing is left to hide.

## Scope

- `Install.ps1` (task **Display Power Profile**; fires on power-source change, resume and logon, so it was flashing a window too).
- `Register-EmuDeckHashWatcher.ps1` (task **EmuDeck Hash Sync Watcher**; the long-running one).
- `Register-KaijaSheetBackup.ps1` (task **Kaija Sheet Backup**).
- Live task **Sync Calibre to NAS**: its registration is not in the repo, so only the live task action is updated.
- Not changed: `Register-SpeakerAmpAutofix.ps1` (runs as SYSTEM in session 0, no desktop, so no window), `Z13Tray.ps1` (starts its helper with a hidden start-up window, which Windows Terminal does not take over), `superseded/`.

## Steps

1. Branch `headless-task-launch` from main.
2. Edit the three scripts; add a README caveat.
3. Re-register the live tasks (and update the Calibre task action).
4. Verify each live task's action, then check after the next sign-in that no Terminal window appears.
5. Commit on the branch; merge only with Josh's approval.

## Rollback

Re-run the old scripts from `main`, or set a task's action back to `powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "..."`.

## Caveats

- A task that is already running keeps its old window until it restarts. The watcher is long-running, so its window disappears at the next sign-in (or when the watcher is restarted).
- **Trade-off found while testing:** `Stop-ScheduledTask` (and the task's execution time limit) ends only `conhost.exe`; the PowerShell it started keeps running. A direct `powershell.exe` task is killed properly. A `wscript.exe` wrapper behaves the same way, so this is inherent to any wrapper, and VBScript is being deprecated by Microsoft, which is why `conhost --headless` was kept. Mitigations: the watcher now takes a named mutex (`Local\Z13Automate.EmuDeckHashWatcher`) so a second copy exits immediately; the other tasks are short and have their own limits (Kaija retries for at most 8 h inside the script). To restart the watcher on purpose, stop the `powershell.exe` whose command line contains `Watch-ESDESessions.ps1` first, then start the task.
- Re-registering a running task (`Register-ScheduledTask -Force`) does not kill the running instance (tested), so the Register scripts are safe to re-run while the watcher is up.
