<#
Repair-SpeakerAmp.ps1
Restart the Z13's Cirrus Logic speaker amp if it failed to start after Modern Standby.

Symptom: speakers silent while Windows shows audio playing. The amp device (ACPI\CSC3551\1)
sometimes re-initialises during standby and fails with Code 10 / 0xC000009E (power failure);
pnputil /restart-device fixes it. Runs as SYSTEM from the "Speaker Amp Autofix" task, from the
protected copy in C:\ProgramData\Z13Automate (see Register-SpeakerAmpAutofix.ps1).
#>
param(
  [string]$DeviceId = 'ACPI\CSC3551\1',
  [string]$Log = 'C:\ProgramData\Z13Automate\speaker-amp.log',
  [int]$SettleSeconds = 8
)

$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force (Split-Path $Log) | Out-Null
function Note($m){ ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) | Out-File -FilePath $Log -Append -Encoding utf8 }

Start-Sleep -Seconds $SettleSeconds   # right after wake the device may still be arriving
$d = Get-PnpDevice -InstanceId $DeviceId -ErrorAction SilentlyContinue
if (-not $d) { Note "amp not present ($DeviceId)"; exit 0 }

if ($d.Status -eq 'OK') { Note 'amp OK' }
elseif ("$($d.Problem)" -eq 'CM_PROB_DISABLED') { Note 'amp is disabled on purpose; leaving it alone' }
else {
  Note "amp in error: $($d.Status) / $($d.Problem) -> restarting"
  $out = (& pnputil.exe /restart-device $DeviceId 2>&1 | Where-Object { "$_".Trim() }) -join ' | '
  Start-Sleep -Seconds 3
  $after = Get-PnpDevice -InstanceId $DeviceId -ErrorAction SilentlyContinue
  Note "pnputil: $out"
  Note "after restart: $($after.Status) / $($after.Problem)"
}

# Keep the log small.
$lines = @(Get-Content $Log -ErrorAction SilentlyContinue)
if ($lines.Count -gt 500) { $lines | Select-Object -Last 500 | Set-Content -Path $Log -Encoding utf8 }
exit 0
