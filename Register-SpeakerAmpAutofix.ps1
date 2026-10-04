# Register-SpeakerAmpAutofix.ps1 - install the "Speaker Amp Autofix" task. Run ELEVATED.
# Copies Repair-SpeakerAmp.ps1 to C:\ProgramData\Z13Automate (writable only by SYSTEM/Administrators,
# because the task runs as SYSTEM) and triggers it on Modern Standby exit, logon, unlock, and every 30 min.

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host 'STOP: run this elevated.' -ForegroundColor Red; exit 1 }

$taskName = 'Speaker Amp Autofix'
$src      = Join-Path $PSScriptRoot 'Repair-SpeakerAmp.ps1'
$dir      = 'C:\ProgramData\Z13Automate'
$dst      = Join-Path $dir 'Repair-SpeakerAmp.ps1'
if (-not (Test-Path $src)) { Write-Host "STOP: missing $src" -ForegroundColor Red; exit 1 }

# Protected folder: SYSTEM + Administrators full control, Users read/execute only (no inherited write).
New-Item -ItemType Directory -Force $dir | Out-Null
& icacls.exe $dir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
Copy-Item $src $dst -Force

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $dst)

# Kernel-Power 507 = exiting Modern Standby (this Z13 never logs the classic resume events 107 / Power-Troubleshooter 1).
$evClass = Get-CimClass -Namespace ROOT\Microsoft\Windows\TaskScheduler -ClassName MSFT_TaskEventTrigger
$trigWake = New-CimInstance -CimClass $evClass -ClientOnly
$trigWake.Enabled = $true
$trigWake.Subscription = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Kernel-Power''] and EventID=507]]</Select></Query></QueryList>'

$ssClass = Get-CimClass -Namespace ROOT\Microsoft\Windows\TaskScheduler -ClassName MSFT_TaskSessionStateChangeTrigger
$trigUnlock = New-CimInstance -CimClass $ssClass -ClientOnly
$trigUnlock.Enabled = $true
$trigUnlock.StateChange = 8   # TASK_SESSION_UNLOCK

$trigLogon = New-ScheduledTaskTrigger -AtLogOn
$trigEvery = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)

$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($trigWake, $trigUnlock, $trigLogon, $trigEvery) `
  -Principal $principal -Settings $settings -Description 'Restarts the Cirrus speaker amp (ACPI\CSC3551\1) if it failed after Modern Standby (Z13 Automate)' -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($t) { Write-Host "Task '$taskName' -> $dst ($($t.Triggers.Count) triggers)" -ForegroundColor Green } else { Write-Host 'Task registration FAILED' -ForegroundColor Red; exit 1 }
