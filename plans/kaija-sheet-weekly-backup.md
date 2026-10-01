# Weekly Kaija character-sheet backup

## Goal

Every Thursday at 9:00 AM, copy `C:\Users\jjmorse\Documents\Kaija.pdf` to `\\KrynnVault\Books\Spell and Blade\Character sheets\` as `Kaija YYYY.MM.DD.pdf`, dated the previous Wednesday, but only if its contents differ from the newest sheet already on the NAS.

## Design

- `Backup-KaijaSheet.ps1` does the work; `Register-KaijaSheetBackup.ps1` creates the scheduled task. Both follow `Sync-CalibreLibrary.ps1` conventions (`Note` logging to `%LOCALAPPDATA%\KaijaSheetBackup\backup.log`, abort on missing source or unreachable NAS).
- Date: the most recent Wednesday strictly before today (a Thursday run uses yesterday; a catch-up run later in the week still uses that week's Wednesday).
- Change detection: SHA-256 of the local PDF vs the newest `Kaija*.pdf` on the NAS (newest = latest `YYYY.MM.DD` in the filename, falling back to LastWriteTime). Size and timestamps are not trusted.
- Never overwrite: if the target name already exists, skip and log.
- Verify the copy by hash after writing.
- NAS unreachable (asleep, away from home, VPN): retry inside the script every 15 minutes for up to 8 hours, because Task Scheduler's "restart on failure" does not fire on a non-zero exit code.
- Task: weekly Thursday 09:00, runs as Josh (interactive, not elevated) so it has his NAS credentials, `StartWhenAvailable` so a missed run (PC off/asleep) runs at next wake, allowed on battery.
- `-DryRun` switch reports what it would do without copying.

## Steps

1. Branch `kaija-sheet-backup` from main.
2. Write both scripts.
3. Dry run, then register the task and run it once via Task Scheduler (2026-10-01, a Thursday -> `Kaija 2026.09.30.pdf`).
4. Update README.
5. Commit on the branch; merge only with Josh's approval.
