# Z13 Automate

Windows automation and maintenance scripts for the **ASUS ROG Flow Z13 (GZ302EA)** — Ryzen AI Max+ 395, Radeon 8060S, Windows 11. The headline feature switches display **refresh rate and brightness by power source** (full speed on AC, battery-saving on DC); the rest are self-contained maintenance scripts collected here so one folder holds everything that keeps this machine tuned.

## What it does

- **Display/power automation** — 180 Hz + 90% brightness on AC, 60 Hz + 40% on battery, applied automatically the moment you plug in or unplug. Refresh rate only changes when undocked (single display), so it never fights an external monitor. A tray app shows the current Hz and lets you pick presets by hand.
- **Windows Update control** — pause updates far past the 35-day UI cap, or fully disable/re-enable the update stack.
- **Claude Desktop config fixes** — repair the `claude_desktop_config.json` issues left by a Windows profile migration (stale paths, `npx` path-with-spaces, UTF-8 BOM corruption).
- **Speaker amp auto-fix** — restarts the Cirrus Logic speaker amp whenever it fails to come back from Modern Standby (silent speakers while Windows shows audio playing).
- **EmuDeck Cloud Sync fix** — after each ES-DE session, refresh EmuDeck's per-emulator cloud `.hash` files so the Steam Deck actually downloads saves made on this PC (EmuDeck for Windows never updates them).
- **Weekly character-sheet backup** — every Thursday 9:00 AM, snapshot `Documents\Kaija.pdf` to the NAS as `Kaija YYYY.MM.DD.pdf` (previous Wednesday's date), only when it changed.

## Quick start (display/power automation)

1. Clone or copy this folder anywhere stable (not a path that moves).
2. Edit `config.json` to taste (see below).
3. Run the installer **elevated** (registers the scheduled task + adds the tray app to startup):

```powershell
powershell -ExecutionPolicy Bypass -File .\Install.ps1
```

That's it — the tray app starts at logon, and the profile re-applies on every power-source change.

## config.json

```json
{
  "AutomationEnabled": true,
  "AC": { "Hz": 180, "Brightness": 90 },
  "DC": { "Hz": 60,  "Brightness": 40 },
  "Presets": [
    { "Name": "Full Power",      "Hz": 180, "Brightness": 90 },
    { "Name": "Battery Gaming",  "Hz": 60,  "Brightness": 40 },
    { "Name": "Battery Reading", "Hz": 60,  "Brightness": 25 },
    { "Name": "Max Endurance",   "Hz": 60,  "Brightness": 15 }
  ],
  "ManageBrightness": true
}
```

- **AC / DC** — the refresh rate and brightness applied when on the charger vs on battery.
- **Presets** — named profiles the tray app exposes for manual switching.
- **ManageBrightness** — when `true`, the scripts set brightness too; set `false` to control refresh rate only and leave brightness alone.

## Files

### Display / power automation
| File | Role |
|---|---|
| `Z13Display.psm1` | Shared module — display enumeration, refresh/brightness setters, profile logic. |
| `Apply-DisplayPower.ps1` | Applies the correct AC/DC profile; the action the scheduled task runs. |
| `Z13Tray.ps1` | System-tray app — shows current Hz, offers preset switching (single-instance), and is the primary trigger for automatic profile switching (reacts to power-source-change notifications, with a 5s timer as a fallback). |
| `Start-Z13Tray.vbs` | Launches the tray app windowless at logon. |
| `config.json` | All user-tunable settings (above). |
| `Install.ps1` | Registers the scheduled task and the startup shortcut. Run elevated. |

### Windows Update
| File | Role |
|---|---|
| `Set-WindowsUpdatePause.ps1` | Pause updates for years via the registry (past the 35-day UI cap). Preferred. |
| `Disable-WindowsUpdate.ps1` | Hard-disable the update services/tasks/policies. |
| `Enable-WindowsUpdate.ps1` | Restore Windows Update to defaults. |

### Claude Desktop config
| File | Role |
|---|---|
| `Fix-ClaudeConfig.ps1` | Fix stale user-files path **and** wrap `npx` MCP commands in `cmd /c`. |
| `Fix-ClaudeUserFilesPath.ps1` | Re-point `coworkUserFilesPath` after a profile rename. |
| `Restore-ClaudeConfig.ps1` | Restore config from a known-good backup. |
| `Restore-And-Lock-ClaudeConfig.ps1` | Restore, then lock the file read-only as a safety net. |

### Bluetooth
| File | Role |
|---|---|
| `Reset-Bluetooth.ps1` | Cycle the MediaTek Bluetooth adapter to recover a dropped BLE device (e.g. a Surface Slim Pen whose link won't reconnect after charging). Self-elevating; pair it with a desktop shortcut for one-click use. |

### Lighting
| File | Role |
|---|---|
| `Set-ArmouryLighting.ps1` | Stops and disables (or with `-Enable`, restores; `-Status`, reports) Armoury Crate's `LightingService`. That service re-applies its own Aura effect at start/wake and overrides G-Helper's static keyboard backlight. With it disabled G-Helper alone controls the lights (Armoury Crate's Aura page stops working; the rest of Armoury Crate is unaffected). Self-elevates for changes. An Armoury Crate/ASUS update may re-enable the service, so check `-Status` if the lights animate again. |

### Input
| File | Role |
|---|---|
| `ClaudeEnterFix.ahk` | AutoHotkey v2 script that remaps plain Enter to Ctrl+Enter while the Claude app is focused, working around the desktop app's tablet-mode "Enter = newline" bug (Shift+Enter still makes a newline). Requires AutoHotkey v2; add to startup to load at login. |

### Backups
| File | Role |
|---|---|
| `Backup-KaijaSheet.ps1` | Copies `C:\Users\jjmorse\Documents\Kaija.pdf` to `\\KrynnVault\Books\Spell and Blade\Character sheets\Kaija YYYY.MM.DD.pdf`, dated the most recent Wednesday before today, **only if** its SHA-256 differs from the newest `Kaija*.pdf` already there. Never overwrites; verifies the copy by hash; retries every 15 min for up to 8 h if the NAS is unreachable. `-DryRun` reports without copying. Log: `%LOCALAPPDATA%\KaijaSheetBackup\backup.log`. |
| `Register-KaijaSheetBackup.ps1` | Creates/updates the **Kaija Sheet Backup** scheduled task: Thursdays 09:00, runs as you (not elevated, so it has your NAS credentials), runs at next wake if the PC was off/asleep. No admin needed. |

### EmuDeck Cloud Sync
| File | Role |
|---|---|
| `Sync-EmuDeckHashes.ps1` | For each emulator folder under `D:\Emulation\saves` with saves changed since `-Since`: uploads it with EmuDeck's own rclone flags/excludes (keys never leave the PC), then writes and uploads a fresh `Emudeck/saves/<emu>/.hash`. The Deck skips its download when that file is unchanged, and EmuDeck on Windows only refreshes the top-level hash. Uses SHA-256 of `<total bytes>|<newest save mtime>` (EmuDeck's size-only hash misses same-size rewrites like an 8 KB Game Boy `.srm`). `-DryRun` lists only. Log: `%LOCALAPPDATA%\EmuDeckHashSync\sync.log`. |
| `Watch-ESDESessions.ps1` | Hidden background loop (10 s poll): when ES-DE has been closed for 15 s, runs `Sync-EmuDeckHashes.ps1` for that session. Kept outside EmuDeck because EmuDeck overwrites its launchers and resets its backend on every launch. |
| `Register-EmuDeckHashWatcher.ps1` | Creates the **EmuDeck Hash Sync Watcher** logon task (runs as you, no time limit, restarts if it dies) and starts it. No admin needed. |

### Audio
| File | Role |
|---|---|
| `Repair-SpeakerAmp.ps1` | Checks the Cirrus Logic speaker amp (`ACPI\CSC3551\1`). If it is in an error state (typically Code 10 / `0xC000009E` power failure after Modern Standby; not when deliberately disabled), runs `pnputil /restart-device` and re-checks. Logs each run to `C:\ProgramData\Z13Automate\speaker-amp.log` (last 500 lines). |
| `Register-SpeakerAmpAutofix.ps1` | Run **elevated**. Copies `Repair-SpeakerAmp.ps1` to `C:\ProgramData\Z13Automate` (only SYSTEM/Administrators can modify it, since the task runs as SYSTEM) and registers the **Speaker Amp Autofix** task: on Kernel-Power 507 (exiting Modern Standby; this Z13 never logs the classic resume events), logon, unlock, and every 30 minutes. Re-run it after editing `Repair-SpeakerAmp.ps1` so the protected copy updates. |
| `EqualizerAPO/` | Backup + docs for the per-device speaker EQ: Equalizer APO installed on the built-in Realtek speakers only, so the reddit-tuned Z13 speaker boost applies to the speakers automatically while headphones stay untouched (no manual switching). See `EqualizerAPO/README.md`. |

## How the automation triggers

The tray app (`Z13Tray.ps1`) is the primary trigger: it subscribes to the OS-level `SystemEvents.PowerModeChanged` notification and launches `Apply-DisplayPower.ps1` the moment the power source changes, plus it re-checks the power source on its own 5-second status timer as a safety net in case that notification is ever missed. The scheduled task remains installed as a backup path — it fires on **Kernel-Power event 105** (power source changed) and **107** (resume from sleep), plus **at logon** — but it has been observed to get killed mid-run by Task Scheduler (`SCHED_S_TASK_TERMINATED`) before it finishes applying, which is why the tray no longer relies on it alone. Event 105 alone isn't enough for the scheduled task either: if the machine boots or wakes *already* on battery there's no change event, so the logon trigger and event 107 cover those cases. Applying the same profile twice is harmless (idempotent), so having both the tray and the scheduled task react to the same change is safe. Brightness writes use a short settle-delay to avoid a race where a write during the AC/DC transition gets attributed to the wrong power source.

## Notes and caveats

- **Windows PowerShell 5.1 gotcha:** never edit `claude_desktop_config.json` with `Set-Content -Encoding UTF8` — it writes a UTF-8 BOM that Node/Electron's `JSON.parse` rejects, silently wiping the config. Use `[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`.
- **Background tasks launch through `conhost.exe --headless`**, not `powershell -WindowStyle Hidden`. Task Scheduler can't start a process hidden, so with Windows Terminal as the default terminal the hidden PowerShell still opened a visible Terminal window at logon (and closing it killed the task). The **Display Power Profile**, **EmuDeck Hash Sync Watcher**, **Kaija Sheet Backup** and **Sync Calibre to NAS** tasks all use it; the SYSTEM speaker-amp task runs in session 0 and doesn't need to. Trade-off: stopping such a task (or hitting its time limit) ends only `conhost`, not the PowerShell it started, so `Watch-ESDESessions.ps1` takes a mutex to stay single-instance. To restart the watcher deliberately, stop its `powershell.exe` first. See `plans/headless-task-launch.md`.
- Most scripts need an **elevated** PowerShell (task registration, service changes, registry writes under HKLM).
- `superseded/` holds earlier versions kept for reference; nothing there is used at runtime.
- Machine-specific: paths and device assumptions target this Z13. Review before running on other hardware.
