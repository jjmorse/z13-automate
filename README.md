# Z13 Automate

Windows automation and maintenance scripts for the **ASUS ROG Flow Z13 (GZ302EA)** — Ryzen AI Max+ 395, Radeon 8060S, Windows 11. The headline feature switches display **refresh rate and brightness by power source** (full speed on AC, battery-saving on DC); the rest are self-contained maintenance scripts collected here so one folder holds everything that keeps this machine tuned.

## What it does

- **Display/power automation** — 180 Hz + 90% brightness on AC, 60 Hz + 40% on battery, applied automatically the moment you plug in or unplug. Refresh rate only changes when undocked (single display), so it never fights an external monitor. A tray app shows the current Hz and lets you pick presets by hand.
- **Windows Update control** — pause updates far past the 35-day UI cap, or fully disable/re-enable the update stack.
- **Claude Desktop config fixes** — repair the `claude_desktop_config.json` issues left by a Windows profile migration (stale paths, `npx` path-with-spaces, UTF-8 BOM corruption).

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
| `Z13Tray.ps1` | System-tray app — shows current Hz, offers preset switching (single-instance). |
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

## How the automation triggers

The scheduled task fires on **Kernel-Power event 105** (power source changed) and **107** (resume from sleep), plus **at logon**. Event 105 alone isn't enough: if the machine boots or wakes *already* on battery there's no change event, so the logon trigger and event 107 cover those cases. Brightness writes use a short settle-delay to avoid a race where a write during the AC↔DC transition gets attributed to the wrong power source.

## Notes and caveats

- **Windows PowerShell 5.1 gotcha:** never edit `claude_desktop_config.json` with `Set-Content -Encoding UTF8` — it writes a UTF-8 BOM that Node/Electron's `JSON.parse` rejects, silently wiping the config. Use `[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`.
- Most scripts need an **elevated** PowerShell (task registration, service changes, registry writes under HKLM).
- `superseded/` holds earlier versions kept for reference; nothing there is used at runtime.
- Machine-specific: paths and device assumptions target this Z13. Review before running on other hardware.
