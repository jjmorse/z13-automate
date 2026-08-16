# Re-apply the coworkUserFilesPath fix if Claude Desktop overwrites it.
#
# Background: the profile migration (jjmor -> jjmorse) left
# claude_desktop_config.json pointing coworkUserFilesPath at the OLD profile
# (C:\Users\jjmor\Claude). Claude Desktop rewrites this file on exit, so an
# edit made while the app is RUNNING can be clobbered.
#
# BEST PRACTICE: quit Claude Desktop completely, THEN run this.

$ErrorActionPreference = 'Stop'
$cfg = "$env:APPDATA\Claude\claude_desktop_config.json"
$OLD = 'C:\\Users\\jjmor\\Claude'      # doubled backslashes = JSON-escaped
$NEW = 'C:\\Users\\jjmorse\\Claude'

if (-not (Test-Path $cfg)) { Write-Host "config not found: $cfg" -ForegroundColor Red; exit 1 }

# Warn if Claude is running - the edit may not survive
$running = Get-Process -Name 'Claude' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "WARNING: Claude Desktop is running (PID $($running.Id -join ', '))." -ForegroundColor Yellow
    Write-Host "         It rewrites this config on exit and may undo this change." -ForegroundColor Yellow
    Write-Host "         Quit Claude Desktop and re-run for a durable fix.`n" -ForegroundColor Yellow
}

# Ensure the target folder exists
$target = "$env:USERPROFILE\Claude"
if (-not (Test-Path $target)) {
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Write-Host "created $target"
}

$content = Get-Content $cfg -Raw
$before = ($content -split "`n" | Select-String 'coworkUserFilesPath').Line
Write-Host "before: $($before.Trim())"

if ($content -notmatch [regex]::Escape($OLD)) {
    Write-Host "already correct - nothing to do" -ForegroundColor Green
    exit 0
}

# Back up, then replace
$bak = "$cfg.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $cfg $bak -Force
Write-Host "backup: $bak"

$new = $content.Replace($OLD, $NEW)
Set-Content $cfg $new -Encoding UTF8 -NoNewline

# Validate
try {
    $null = Get-Content $cfg -Raw | ConvertFrom-Json
    $after = ((Get-Content $cfg -Raw) -split "`n" | Select-String 'coworkUserFilesPath').Line
    Write-Host "after : $($after.Trim())" -ForegroundColor Green
    Write-Host "JSON valid" -ForegroundColor Green
} catch {
    Write-Host "INVALID JSON after edit - restoring backup" -ForegroundColor Red
    Copy-Item $bak $cfg -Force
    exit 1
}
