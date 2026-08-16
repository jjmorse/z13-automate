# Disable Windows automatic updates + block forced restarts. Run ELEVATED.
# Revert with Enable-WindowsUpdate.ps1 in the same folder.
#
# Layered on purpose:
#   1. Policy NoAutoUpdate            - stops automatic download/install
#   2. Policy NoAutoRebootWithLoggedOnUsers - blocks forced restart even if
#                                        Windows re-enables the services
#   3. Services disabled              - wuauserv / UsoSvc
#   4. UpdateOrchestrator tasks       - disabled, they re-trigger updates
#
# You can still update MANUALLY: run Enable-WindowsUpdate.ps1, check for
# updates, then re-run this script.

$ErrorActionPreference = 'Continue'
$out = "$PSScriptRoot\wu-disable-result.txt"
$L = @()

function Log($m) { $script:L += $m; Write-Host $m }

Log "=== 1. Policies ==="
$au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
try {
    if (-not (Test-Path $au)) { New-Item -Path $au -Force -EA Stop | Out-Null }
    New-ItemProperty -Path $au -Name 'NoAutoUpdate'                  -Value 1 -PropertyType DWord -Force -EA Stop | Out-Null
    New-ItemProperty -Path $au -Name 'NoAutoRebootWithLoggedOnUsers' -Value 1 -PropertyType DWord -Force -EA Stop | Out-Null
    New-ItemProperty -Path $au -Name 'AUOptions'                     -Value 2 -PropertyType DWord -Force -EA Stop | Out-Null
    Log "  NoAutoUpdate=1, NoAutoRebootWithLoggedOnUsers=1, AUOptions=2 (notify only)"
} catch { Log "  policy FAILED: $($_.Exception.Message)" }

Log ""
Log "=== 2. Services ==="
foreach ($svc in 'wuauserv','UsoSvc','WaaSMedicSvc') {
    try {
        $s = Get-Service $svc -EA Stop
        if ($s.Status -eq 'Running') { Stop-Service $svc -Force -EA SilentlyContinue }
        Set-Service $svc -StartupType Disabled -EA Stop
        Log "  $svc -> Disabled"
    } catch {
        # WaaSMedicSvc is protected; fall back to the registry Start value
        try {
            Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$svc" -Name Start -Value 4 -EA Stop
            Log "  $svc -> Disabled (via registry Start=4)"
        } catch { Log "  $svc FAILED: $($_.Exception.Message)" }
    }
}

Log ""
Log "=== 3. UpdateOrchestrator scheduled tasks ==="
$paths = '\Microsoft\Windows\UpdateOrchestrator\','\Microsoft\Windows\WindowsUpdate\'
foreach ($p in $paths) {
    Get-ScheduledTask -TaskPath $p -EA SilentlyContinue | ForEach-Object {
        try { Disable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath -EA Stop | Out-Null
              Log "  disabled: $($_.TaskPath)$($_.TaskName)" }
        catch { Log "  could not disable: $($_.TaskName) ($($_.Exception.Message))" }
    }
}

Log ""
Log "=== VERIFY ==="
$p = Get-ItemProperty $au -EA SilentlyContinue
Log "  NoAutoUpdate                  = $($p.NoAutoUpdate)"
Log "  NoAutoRebootWithLoggedOnUsers = $($p.NoAutoRebootWithLoggedOnUsers)"
Get-Service wuauserv,UsoSvc,WaaSMedicSvc -EA SilentlyContinue |
    ForEach-Object { Log ("  {0,-14} {1,-8} {2}" -f $_.Name, $_.Status, $_.StartType) }

$L -join "`n" | Set-Content $out -Encoding UTF8
Write-Host "`nWritten to $out"
