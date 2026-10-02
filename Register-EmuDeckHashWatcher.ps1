# Register-EmuDeckHashWatcher.ps1 - create/update the "EmuDeck Hash Sync Watcher" task and start it.
# Runs Watch-ESDESessions.ps1 hidden at logon as the current user (not elevated). No admin needed.

$ErrorActionPreference = 'Stop'
$taskName = 'EmuDeck Hash Sync Watcher'
$script   = Join-Path $PSScriptRoot 'Watch-ESDESessions.ps1'
if (-not (Test-Path $script)) { Write-Host "STOP: missing $script" -ForegroundColor Red; exit 1 }

$action    = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script)
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
# Long-running watcher: no time limit, keep running on battery, restart if it dies.
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
             -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
  -Settings $settings -Description 'Refreshes EmuDeck per-emulator cloud .hash files after ES-DE sessions so the Steam Deck downloads Z13 saves (Z13 Automate)' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$s = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "Task '$taskName' registered; state: $s" -ForegroundColor Green
