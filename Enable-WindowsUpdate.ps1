# Restore Windows Update to defaults (undo Disable-WindowsUpdate.ps1).
# Run ELEVATED.
#
# Use this when you WANT to patch: run it, check for updates in Settings,
# install, then re-run Disable-WindowsUpdate.ps1 if you want it off again.

$ErrorActionPreference = 'Continue'
$out = "$PSScriptRoot\wu-enable-result.txt"
$L = @()
function Log($m) { $script:L += $m; Write-Host $m }

Log "=== 1. Remove policies ==="
$au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
foreach ($n in 'NoAutoUpdate','NoAutoRebootWithLoggedOnUsers','AUOptions') {
    try { Remove-ItemProperty -Path $au -Name $n -EA Stop; Log "  removed $n" }
    catch { Log "  $n not present" }
}

Log ""
Log "=== 2. Services back to default ==="
# defaults: wuauserv=Manual, UsoSvc=Automatic, WaaSMedicSvc=Manual
$defaults = @{ wuauserv='Manual'; UsoSvc='Automatic'; WaaSMedicSvc='Manual' }
foreach ($svc in $defaults.Keys) {
    try {
        Set-Service $svc -StartupType $defaults[$svc] -EA Stop
        Log "  $svc -> $($defaults[$svc])"
    } catch {
        $startVal = if ($defaults[$svc] -eq 'Automatic') { 2 } else { 3 }
        try { Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$svc" -Name Start -Value $startVal -EA Stop
              Log "  $svc -> $($defaults[$svc]) (via registry)" }
        catch { Log "  $svc FAILED: $($_.Exception.Message)" }
    }
}

Log ""
Log "=== 3. Re-enable scheduled tasks ==="
foreach ($p in '\Microsoft\Windows\UpdateOrchestrator\','\Microsoft\Windows\WindowsUpdate\') {
    Get-ScheduledTask -TaskPath $p -EA SilentlyContinue | ForEach-Object {
        try { Enable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath -EA Stop | Out-Null
              Log "  enabled: $($_.TaskPath)$($_.TaskName)" }
        catch { Log "  could not enable: $($_.TaskName)" }
    }
}

Log ""
Log "=== 4. Start the service ==="
try { Start-Service wuauserv -EA Stop; Log "  wuauserv started" } catch { Log "  wuauserv: $($_.Exception.Message)" }

Log ""
Log "=== VERIFY ==="
Get-Service wuauserv,UsoSvc,WaaSMedicSvc -EA SilentlyContinue |
    ForEach-Object { Log ("  {0,-14} {1,-8} {2}" -f $_.Name, $_.Status, $_.StartType) }
Log "  Now open Settings > Windows Update and 'Check for updates'."

$L -join "`n" | Set-Content $out -Encoding UTF8
