# Install.ps1 — point the scheduled task at the new script location and add the
# tray app to startup. Run ELEVATED (task update needs admin).

$ErrorActionPreference = 'Stop'
$root     = $PSScriptRoot
$taskName = 'Display Power Profile'
$applyPs1 = Join-Path $root 'Apply-DisplayPower.ps1'
$trayVbs  = Join-Path $root 'Start-Z13Tray.vbs'

foreach ($f in @($applyPs1, $trayVbs)) {
  if (-not (Test-Path $f)) { Write-Host "STOP: missing $f" -ForegroundColor Red; exit 1 }
}

# ---- 1. Update scheduled task action to new path ----
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $applyPs1)

# Triggers. EventID 105 alone is NOT enough - it only fires on a power source
# CHANGE. If the machine boots or resumes from sleep already on battery there is
# no change event, so the profile is never applied and the wrong one persists.
# (Observed 2026-07-23: ran the AC profile on battery for hours because of this.)
# 105 = power source changed, 107 = resumed from sleep. Plus a logon trigger.
$class = Get-CimClass -Namespace ROOT\Microsoft\Windows\TaskScheduler -ClassName MSFT_TaskEventTrigger
$trigEvent = New-CimInstance -CimClass $class -ClientOnly
$trigEvent.Enabled = $true
$trigEvent.Subscription = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Kernel-Power''] and (EventID=105 or EventID=107)]]</Select></Query></QueryList>'

$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$trig = @($trigEvent, $trigLogon)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trig `
  -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($t) {
  Write-Host "Task '$taskName' -> $applyPs1" -ForegroundColor Green
  Write-Host "  DisallowStartIfOnBatteries: $($t.Settings.DisallowStartIfOnBatteries) (want False)"
  Write-Host "  StopIfGoingOnBatteries    : $($t.Settings.StopIfGoingOnBatteries) (want False)"
} else { Write-Host 'Task registration FAILED' -ForegroundColor Red; exit 1 }

# ---- 2. Startup shortcut for the tray app ----
$startup = [Environment]::GetFolderPath('Startup')
$lnk     = Join-Path $startup 'Z13 Automate Tray.lnk'
$ws = New-Object -ComObject WScript.Shell
$s  = $ws.CreateShortcut($lnk)
$s.TargetPath       = 'wscript.exe'
$s.Arguments        = '"' + $trayVbs + '"'
$s.WorkingDirectory = $root
$s.Description      = 'Z13 Automate — display/power tray app'
$s.Save()
Write-Host "Startup shortcut: $lnk" -ForegroundColor Green

Write-Host "`nDone. Tray starts at logon; task fires on power-source change." -ForegroundColor Cyan
