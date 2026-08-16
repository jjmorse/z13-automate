# Register a scheduled task that runs apply-display-power.ps1 whenever the
# power source changes (Kernel-Power EventID 105). Run ELEVATED.

$ErrorActionPreference = 'Stop'
$taskName = 'Display Power Profile'
$script   = 'C:\Users\jjmorse\OneDrive\Documents\Claude\apply-display-power.ps1'

if (-not (Test-Path $script)) { Write-Host "STOP: $script not found" -ForegroundColor Red; exit 1 }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script)

# Event trigger: Kernel-Power 105 (power source changed)
$class = Get-CimClass -Namespace ROOT\Microsoft\Windows\TaskScheduler -ClassName MSFT_TaskEventTrigger
$trig  = New-CimInstance -CimClass $class -ClientOnly
$trig.Enabled = $true
$trig.Subscription = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Kernel-Power''] and (EventID=105)]]</Select></Query></QueryList>'

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

# CRITICAL: allow running on battery, and don't kill it when going to battery
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trig `
  -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($t) {
  Write-Host "Registered: $taskName  (state: $($t.State))" -ForegroundColor Green
  Write-Host "Trigger: Kernel-Power EventID 105 (power source change)"
  Write-Host "Runs on battery: yes (AllowStartIfOnBatteries set)"
} else { Write-Host "FAILED to register" -ForegroundColor Red; exit 1 }
