# Fix two leftovers in claude_desktop_config.json. RUN WITH CLAUDE DESKTOP CLOSED.
#
#   1. coworkUserFilesPath still points at the OLD profile (C:\Users\jjmor\Claude)
#      from the jjmor -> jjmorse migration.
#   2. The "acc" MCP server fails on Windows with:
#         'C:\Program' is not recognized as an internal or external command
#      Claude launches MCP servers through cmd.exe, and a bare "npx" command can
#      break on paths containing spaces (npx.cmd lives in C:\Program Files\nodejs).
#      The documented Windows fix is to invoke it explicitly via cmd /c.
#
# Claude Desktop REWRITES this config on exit, so an edit made while it is
# running gets clobbered. Close it first.
#
#   powershell -ExecutionPolicy Bypass -File "<this file>"

$ErrorActionPreference = 'Stop'
$cfg = "$env:APPDATA\Claude\claude_desktop_config.json"

if (-not (Test-Path $cfg)) { Write-Host "config not found: $cfg" -ForegroundColor Red; exit 1 }

$running = Get-Process -Name 'Claude' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "STOP: Claude Desktop is running (PID $($running.Id -join ', '))." -ForegroundColor Red
    Write-Host "      It rewrites this config on exit and WILL undo these changes." -ForegroundColor Red
    Write-Host "      Quit Claude Desktop completely, then re-run." -ForegroundColor Red
    exit 1
}

# --- backup ---
$bak = "$cfg.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $cfg $bak -Force
Write-Host "backup: $bak`n"

$json = Get-Content $cfg -Raw | ConvertFrom-Json

# --- 1. user files path ---
Write-Host "=== coworkUserFilesPath ===" -ForegroundColor Cyan
Write-Host "  before: $($json.coworkUserFilesPath)"
$target = "$env:USERPROFILE\Claude"
if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
$json.coworkUserFilesPath = $target
Write-Host "  after : $($json.coworkUserFilesPath)" -ForegroundColor Green

# --- 2. MCP server command: wrap npx in cmd /c ---
Write-Host "`n=== MCP servers ===" -ForegroundColor Cyan
foreach ($p in $json.mcpServers.PSObject.Properties) {
    $srv = $p.Value
    Write-Host "  [$($p.Name)] command was: $($srv.command)"
    if ($srv.command -eq 'npx') {
        # cmd /c npx <original args...>
        $newArgs = @('/c','npx') + $srv.args
        $srv.command = 'cmd'
        $srv.args    = $newArgs
        Write-Host "    -> cmd /c npx $($srv.args[2..($srv.args.Count-1)] -join ' ')" -ForegroundColor Green
    } else {
        Write-Host "    (left unchanged)"
    }
}

# --- write + validate ---
$json | ConvertTo-Json -Depth 20 | Set-Content $cfg -Encoding UTF8
try {
    $null = Get-Content $cfg -Raw | ConvertFrom-Json
    Write-Host "`nJSON valid. Restart Claude Desktop." -ForegroundColor Green
} catch {
    Copy-Item $bak $cfg -Force
    Write-Host "`nINVALID JSON - backup restored" -ForegroundColor Red
    exit 1
}
