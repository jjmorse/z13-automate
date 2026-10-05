# Set-ArmouryLighting.ps1
#
# Stops (or restores) Armoury Crate's LightingService. That service keeps its own
# Aura effect and re-applies it at service start / wake, overriding whatever G-Helper
# set on the keyboard backlight (a "static green" turning into an animated effect).
# With it disabled, G-Helper alone controls the lights. Armoury Crate's Aura page stops
# working; the rest of Armoury Crate (GameVisual, Scenario Profiles) is unaffected.
#
#   .\Set-ArmouryLighting.ps1            stop + disable the service (default)
#   .\Set-ArmouryLighting.ps1 -Enable    set it back to Automatic and start it (undo)
#   .\Set-ArmouryLighting.ps1 -Status    report only; no elevation needed
#
# Changing the service self-elevates (one UAC prompt). An Armoury Crate / ASUS update
# can re-enable the service: if the lights animate again, check -Status and re-run.
param(
    [switch]$Enable,
    [switch]$Status
)

$name = 'LightingService'

function Show-State {
    $s = Get-CimInstance Win32_Service -Filter "Name='$name'" -ErrorAction SilentlyContinue
    if (-not $s) { Write-Host "$name is not installed." -ForegroundColor Yellow; return $false }
    Write-Host ("{0}: {1}, startup {2}" -f $name, $s.State, $s.StartMode)
    return $true
}

if ($Status) { [void](Show-State); exit 0 }

# --- self-elevate ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" + $(if ($Enable) { ' -Enable' } else { '' })
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

$ErrorActionPreference = 'Stop'
if (-not (Show-State)) { Start-Sleep -Seconds 3; exit 1 }

if ($Enable) {
    Set-Service -Name $name -StartupType Automatic
    Start-Service -Name $name
    Write-Host 'Armoury Crate lighting restored.' -ForegroundColor Green
} else {
    Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
    Set-Service -Name $name -StartupType Disabled
    Write-Host 'Armoury Crate lighting disabled; G-Helper now controls the keyboard lights.' -ForegroundColor Green
    Write-Host 'Click your color in G-Helper once to re-apply it.' -ForegroundColor Green
}
[void](Show-State)
Start-Sleep -Seconds 3
