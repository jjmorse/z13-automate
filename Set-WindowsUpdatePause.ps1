# Replace the failed service-disable approach with Windows' own "Pause updates"
# mechanism, extended far past the 35-day UI cap via the same registry keys
# the Settings toggle writes.
#
# WHY THIS SHOULD HOLD WHERE THE SERVICE-DISABLE DIDN'T:
# WaaSMedicSvc + the Reboot_AC/Reboot_BAT scheduled tasks exist specifically to
# repair a Windows Update configuration that looks broken/tampered-with. A
# legitimately paused system is not broken - it's the exact state Microsoft's
# own UI produces - so there is nothing for the medic service to "fix". We keep
# wuauserv/UsoSvc enabled and running throughout; we are not fighting them.
#
# Defender security intelligence updates are NOT affected by this pause - they
# install regardless (confirmed independently, by design).
#
# Run ELEVATED.

$ErrorActionPreference = 'Continue'
$out = "$PSScriptRoot\wu-pause-result.txt"
$L = @()
function Log($m) { $script:L += $m; Write-Host $m }

# --- 1. Revert the old (ineffective) policy block ---
Log "=== 1. Removing AU policy keys ==="
$au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
foreach ($n in 'NoAutoUpdate','NoAutoRebootWithLoggedOnUsers','AUOptions') {
    try { Remove-ItemProperty -Path $au -Name $n -EA Stop; Log "  removed $n" }
    catch { Log "  $n not present" }
}

Log "`n=== 2. Restoring services to normal defaults ==="
$defaults = @{ wuauserv='Manual'; UsoSvc='Automatic'; WaaSMedicSvc='Manual' }
foreach ($svc in $defaults.Keys) {
    try { Set-Service $svc -StartupType $defaults[$svc] -EA Stop; Log "  $svc -> $($defaults[$svc])" }
    catch {
        $startVal = if ($defaults[$svc] -eq 'Automatic') { 2 } else { 3 }
        try { Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$svc" -Name Start -Value $startVal -EA Stop
              Log "  $svc -> $($defaults[$svc]) (via registry)" }
        catch { Log "  $svc FAILED: $($_.Exception.Message)" }
    }
}
try { Start-Service wuauserv -EA Stop; Log "  wuauserv started" } catch { Log "  wuauserv start: $($_.Exception.Message)" }

Log "`n=== 3. Re-enabling scheduled tasks ==="
foreach ($p in '\Microsoft\Windows\UpdateOrchestrator\','\Microsoft\Windows\WindowsUpdate\') {
    Get-ScheduledTask -TaskPath $p -EA SilentlyContinue | ForEach-Object {
        try { Enable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath -EA Stop | Out-Null
              Log "  enabled: $($_.TaskPath)$($_.TaskName)" }
        catch { Log "  could not enable: $($_.TaskName)" }
    }
}

# --- 4. Set the far-future pause (the real fix) ---
Log "`n=== 4. Setting extended pause (10 years) ==="
$ux = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
if (-not (Test-Path $ux)) { New-Item -Path $ux -Force | Out-Null }

$startUtc  = (Get-Date).ToUniversalTime()
$expiryUtc = $startUtc.AddYears(10)
$startStr  = $startUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
$expiryStr = $expiryUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')

$values = @{
    'PauseUpdatesStartTime'         = $startStr
    'PauseUpdatesExpiryTime'        = $expiryStr
    'PauseFeatureUpdatesStartTime'  = $startStr
    'PauseFeatureUpdatesEndTime'    = $expiryStr
    'PauseQualityUpdatesStartTime'  = $startStr
    'PauseQualityUpdatesEndTime'    = $expiryStr
}
foreach ($k in $values.Keys) {
    New-ItemProperty -Path $ux -Name $k -Value $values[$k] -PropertyType String -Force | Out-Null
    Log "  $k = $($values[$k])"
}

# Force the UX to pick up the new values (documented workaround for UI sync)
Log "`n=== 5. Restarting services so UX re-reads the pause window ==="
try { Restart-Service wuauserv -Force -EA Stop; Log "  wuauserv restarted" } catch { Log "  wuauserv restart: $($_.Exception.Message)" }
try { Restart-Service UsoSvc -Force -EA Stop; Log "  UsoSvc restarted" } catch { Log "  UsoSvc restart: $($_.Exception.Message)" }

# --- Verify ---
Log "`n=== VERIFY ==="
$verify = Get-ItemProperty $ux -EA SilentlyContinue
foreach ($k in $values.Keys) { Log ("  {0,-30} = {1}" -f $k, $verify.$k) }
Log ""
Get-Service wuauserv,UsoSvc,WaaSMedicSvc -EA SilentlyContinue | ForEach-Object { Log ("  {0,-14} {1,-8} {2}" -f $_.Name, $_.Status, $_.StartType) }
$auCheck = Get-ItemProperty $au -EA SilentlyContinue
Log "`n  old AU policy keys remaining: $($auCheck.PSObject.Properties.Name -join ', ')"

$L -join "`n" | Set-Content $out -Encoding UTF8
Write-Host "`nWritten to $out"
