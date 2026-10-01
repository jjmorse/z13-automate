# Register-KaijaSheetBackup.ps1 - create/update the weekly "Kaija Sheet Backup" task.
# Thursdays 09:00, runs as the current user (needs their NAS credentials), not elevated.
# StartWhenAvailable: if the PC is off/asleep at 09:00, it runs at the next opportunity.

$ErrorActionPreference = 'Stop'
$taskName = 'Kaija Sheet Backup'
$script   = Join-Path $PSScriptRoot 'Backup-KaijaSheet.ps1'
if (-not (Test-Path $script)) { Write-Host "STOP: missing $script" -ForegroundColor Red; exit 1 }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script)
$trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At '09:00'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
# 9 h limit covers the script's own 8 h NAS-retry window.
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 9)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Description 'Weekly Kaija.pdf snapshot to KrynnVault if changed (Z13 Automate)' -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($t) { Write-Host "Task '$taskName' -> $script; next run $((Get-ScheduledTaskInfo -TaskName $taskName).NextRunTime)" -ForegroundColor Green }
else { Write-Host 'Task registration FAILED' -ForegroundColor Red; exit 1 }
