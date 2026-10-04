# Speaker amp auto-fix

## Problem

The built-in speakers go silent while Windows still shows audio playing. The "Cirrus Logic Awesome Speaker Amps" device (`ACPI\CSC3551\1`, CS35L41 smart amps) sometimes re-initialises during Modern Standby and fails to start: Code 10 (`CM_PROB_FAILED_START`), status `0xC000009E` (STATUS_DEVICE_POWER_FAILURE). Seen 2026-09-27 22:58 and 2026-10-02 02:44, both after BIOS 314 (installed 2026-09-26) and with the 2024-06-01 Cirrus driver 21.51.46.157; ASUS has no newer driver as of 2026-10-03. `pnputil /restart-device ACPI\CSC3551\1` (admin) fixes it instantly. The same Code 10 after sleep is reported on other ROG models.

## Fix

- `Repair-SpeakerAmp.ps1`: wait a few seconds for the device to settle, check the amp; if it is in an error state (but not deliberately disabled), run `pnputil /restart-device` and re-check. Logs every run to `C:\ProgramData\Z13Automate\speaker-amp.log` (trimmed to the last 500 lines).
- `Register-SpeakerAmpAutofix.ps1` (run elevated): copies the script to `C:\ProgramData\Z13Automate\` with an ACL that only SYSTEM and Administrators can modify (a SYSTEM task must not run a user-writable script), then registers the **Speaker Amp Autofix** task as SYSTEM with triggers:
  - Kernel-Power 507 (exiting Modern Standby): this machine logs ~12/day; the classic resume events 107 and Power-Troubleshooter 1 never appear here,
  - logon and workstation unlock (any user),
  - every 30 minutes as a safety net.

## Steps

1. Branch `speaker-amp-autofix` from main.
2. Write both scripts; register (UAC); run the task once and confirm the log.
3. Document in README; commit; merge only with Josh's approval.
