# Restore config keys that Claude Desktop silently dropped after the previous
# fix (it appears to hold an in-memory preferences model that does not include
# mcpServers and several other keys, and flushes that model back to disk on
# some routine action, wiping anything outside it).
#
# THIS MUST RUN WITH CLAUDE DESKTOP FULLY CLOSED (all claude.exe processes,
# check Task Manager / tray - not just the window). If it is running, this
# script's fix will likely be wiped again the moment the app next saves.
#
#   powershell -ExecutionPolicy Bypass -File "<this file>"
#
# Strategy: merge. Take the PRE-FIX backup (which has the full mcpServers
# block + all the preference keys) as the base, but:
#   - keep coworkUserFilesPath pointed at the CURRENT (correct) jjmorse path
#   - apply the cmd /c npx wrapper fix to the acc MCP server
#   - overlay any preference keys the CURRENT file has that the backup lacks

$ErrorActionPreference = 'Stop'
$cfg = "$env:APPDATA\Claude\claude_desktop_config.json"

$running = Get-Process -Name 'Claude' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "STOP: Claude Desktop is still running ($($running.Count) processes)." -ForegroundColor Red
    Write-Host "      Fully quit it (check Task Manager for ALL claude.exe) before running this." -ForegroundColor Red
    exit 1
}

# Find the pre-fix backup (the one made at 07:44, before mcpServers vanished)
$preFixBak = "$cfg.bak-20260808-074836"
if (-not (Test-Path $preFixBak)) {
    Write-Host "STOP: expected backup not found: $preFixBak" -ForegroundColor Red
    Write-Host "      Available backups:" -ForegroundColor Yellow
    Get-ChildItem "$cfg.bak-*" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "        $($_.Name)" }
    exit 1
}

if (-not (Test-Path $cfg)) { Write-Host "current config not found: $cfg" -ForegroundColor Red; exit 1 }

# Backup the current (broken/incomplete) state before we touch anything
$safety = "$cfg.bak-before-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $cfg $safety -Force
Write-Host "safety backup of current (incomplete) config: $safety`n"

$base    = Get-Content $preFixBak -Raw | ConvertFrom-Json   # rich, has mcpServers, old path
$current = Get-Content $cfg -Raw | ConvertFrom-Json          # correct path, fewer keys

Write-Host "=== Base (pre-fix backup) top-level keys ===" -ForegroundColor Cyan
$base.PSObject.Properties.Name | ForEach-Object { "  $_" }

# 1. Fix the path on the base object
Write-Host "`n=== Applying corrected path ===" -ForegroundColor Cyan
Write-Host "  was: $($base.coworkUserFilesPath)"
$base.coworkUserFilesPath = $current.coworkUserFilesPath
Write-Host "  now: $($base.coworkUserFilesPath)" -ForegroundColor Green

# 2. Apply the cmd /c npx fix to mcpServers.acc
Write-Host "`n=== Applying MCP server fix ===" -ForegroundColor Cyan
foreach ($p in $base.mcpServers.PSObject.Properties) {
    $srv = $p.Value
    if ($srv.command -eq 'npx') {
        $newArgs = @('/c','npx') + $srv.args
        $srv.command = 'cmd'
        $srv.args    = $newArgs
        Write-Host "  [$($p.Name)] -> cmd /c npx ..." -ForegroundColor Green
    }
}

# 3. Overlay any NEW preference keys present in current but not in base
#    (in case anything genuinely new was added since the backup)
Write-Host "`n=== Merging any newer preference keys ===" -ForegroundColor Cyan
if ($current.preferences) {
    foreach ($cp in $current.preferences.PSObject.Properties) {
        if (-not $base.preferences.PSObject.Properties[$cp.Name]) {
            $base.preferences | Add-Member -NotePropertyName $cp.Name -NotePropertyValue $cp.Value -Force
            Write-Host "  added new key: preferences.$($cp.Name)"
        }
    }
}

# --- write + validate ---
$base | ConvertTo-Json -Depth 20 | Set-Content $cfg -Encoding UTF8
try {
    $verify = Get-Content $cfg -Raw | ConvertFrom-Json
    Write-Host "`n=== VERIFY ===" -ForegroundColor Cyan
    Write-Host "  mcpServers present : $([bool]$verify.mcpServers)"
    Write-Host "  coworkUserFilesPath: $($verify.coworkUserFilesPath)"
    Write-Host "  top-level keys     : $($verify.PSObject.Properties.Name -join ', ')"
    Write-Host "`nJSON valid. Restore complete. Launch Claude Desktop." -ForegroundColor Green
} catch {
    Copy-Item $safety $cfg -Force
    Write-Host "`nINVALID JSON - safety backup restored" -ForegroundColor Red
    exit 1
}
