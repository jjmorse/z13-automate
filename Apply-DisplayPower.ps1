# Apply-DisplayPower.ps1
# Run by the "Display Power Profile" scheduled task on Kernel-Power EventID 105
# (power source change). Applies the AC or DC profile from config.json.
#
#   Apply-DisplayPower.ps1            - auto-detect power source
#   Apply-DisplayPower.ps1 -Force DC  - pretend on battery (testing)
#   Apply-DisplayPower.ps1 -Force AC  - pretend on AC (testing)
#
# NOTE: keep this file pure ASCII. PowerShell 5.1 reads BOM-less .ps1 as ANSI,
# so UTF-8 punctuation (em-dashes, curly quotes) decodes into stray characters
# that can break string literals and cause parse errors.

param([ValidateSet('AC','DC')][string]$Force)

Import-Module (Join-Path $PSScriptRoot 'Z13Display.psm1') -Force

$cfg = Get-Z13Config

if ((-not $cfg.AutomationEnabled) -and (-not $Force)) {
    Write-Z13Log "automation disabled in config - skipping"
    "automation disabled"
    return
}

if ($Force) {
    $p = if ($Force -eq 'DC') { $cfg.DC } else { $cfg.AC }
    Invoke-Z13Profile -Hz $p.Hz -Brightness $p.Brightness -Reason "forced/$Force"
    $src = $Force
}
else {
    $src = Invoke-Z13AutoProfile
}

$displays = Get-DisplayCount
$hz       = Get-CurrentRefresh
$bright   = Get-CurrentBrightness
"source=$src displays=$displays refresh=$($hz)Hz brightness=$($bright)%"
