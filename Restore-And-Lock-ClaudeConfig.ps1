# Restore the full config (mcpServers + corrected path) AND set it read-only,
# as an experiment to see if that stops Claude Desktop's settings-flush from
# wiping mcpServers on startup (known bug: anthropics/claude-code #32345, #34359).
#
# MUST run with Claude Desktop FULLY CLOSED (check Task Manager for ALL
# claude.exe processes - not just the window).
#
#   powershell -ExecutionPolicy Bypass -File "<this file>"
#
# WHAT TO WATCH FOR AFTER RELAUNCHING:
#   - GOOD: Developer section still shows the "acc" server after using the app
#     for a bit (try changing something like theme or window size, since those
#     are among the settings that trigger a flush).
#   - BAD (attribute enforced but breaks the app): a visible error/toast about
#     failing to save settings, or theme/window-size changes not persisting.
#   - BAD (experiment doesn't work): mcpServers vanishes again anyway - meaning
#     the app cleared the read-only attribute itself or used a write pattern
#     that succeeds regardless. If so, tell me and I'll remove the read-only
#     attribute since it'd be doing nothing but risking the app.
#
# To undo the read-only lock at any time:
#   Set-ItemProperty "$env:APPDATA\Claude\claude_desktop_config.json" -Name IsReadOnly -Value $false

$ErrorActionPreference = 'Stop'
$cfg = "$env:APPDATA\Claude\claude_desktop_config.json"

$running = Get-Process -Name 'Claude' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "STOP: Claude Desktop is still running ($($running.Count) processes)." -ForegroundColor Red
    Write-Host "      Fully quit it (check Task Manager for ALL claude.exe) before running this." -ForegroundColor Red
    exit 1
}

$preFixBak = "$cfg.bak-20260808-074836"
if (-not (Test-Path $preFixBak)) {
    Write-Host "STOP: expected backup not found: $preFixBak" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $cfg)) { Write-Host "current config not found: $cfg" -ForegroundColor Red; exit 1 }

# In case the file is already read-only from a prior attempt, clear it so we can write.
Set-ItemProperty $cfg -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue

$safety = "$cfg.bak-before-lock-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $cfg $safety -Force
Write-Host "safety backup: $safety`n"

$base    = Get-Content $preFixBak -Raw | ConvertFrom-Json
$current = Get-Content $cfg -Raw | ConvertFrom-Json

Write-Host "=== Applying corrected path ===" -ForegroundColor Cyan
Write-Host "  was: $($base.coworkUserFilesPath)"
$base.coworkUserFilesPath = $current.coworkUserFilesPath
Write-Host "  now: $($base.coworkUserFilesPath)" -ForegroundColor Green

Write-Host "`n=== Applying MCP server fix (cmd /c npx) ===" -ForegroundColor Cyan
foreach ($p in $base.mcpServers.PSObject.Properties) {
    $srv = $p.Value
    if ($srv.command -eq 'npx') {
        $srv.command = 'cmd'
        $srv.args    = @('/c','npx') + $srv.args
        Write-Host "  [$($p.Name)] -> cmd /c npx ..." -ForegroundColor Green
    }
}

Write-Host "`n=== Merging any newer preference keys from current ===" -ForegroundColor Cyan
if ($current.preferences) {
    foreach ($cp in $current.preferences.PSObject.Properties) {
        if (-not $base.preferences.PSObject.Properties[$cp.Name]) {
            $base.preferences | Add-Member -NotePropertyName $cp.Name -NotePropertyValue $cp.Value -Force
            Write-Host "  added: preferences.$($cp.Name)"
        }
    }
}

$base | ConvertTo-Json -Depth 20 | Set-Content $cfg -Encoding UTF8

try {
    $verify = Get-Content $cfg -Raw | ConvertFrom-Json
    Write-Host "`n=== Content verify ===" -ForegroundColor Cyan
    Write-Host "  mcpServers present : $([bool]$verify.mcpServers)"
    Write-Host "  coworkUserFilesPath: $($verify.coworkUserFilesPath)"
} catch {
    Copy-Item $safety $cfg -Force
    Write-Host "INVALID JSON - safety backup restored, NOT locking" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== Locking file read-only ===" -ForegroundColor Cyan
Set-ItemProperty $cfg -Name IsReadOnly -Value $true
$attr = (Get-Item $cfg).IsReadOnly
Write-Host "  IsReadOnly = $attr" -ForegroundColor $(if ($attr) { 'Green' } else { 'Red' })

Write-Host "`nDone. Launch Claude Desktop and check the Developer section." -ForegroundColor Cyan
Write-Host "Try changing a setting (theme/window size) to force a save cycle, then re-check."
