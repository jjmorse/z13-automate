<#
Sync-EmuDeckHashes.ps1
Work around an EmuDeck (Windows) Cloud Sync bug so the Steam Deck downloads saves made here.

The Deck's per-emulator download skips when the cloud Emudeck/saves/<emu>/.hash matches its
local copy. EmuDeck on Windows only refreshes the top-level saves\.hash during ES-DE sessions,
so per-emulator hashes go stale and the Deck never pulls Z13 saves. After an ES-DE session this
script uploads each emulator folder with saves changed since -Since, then writes and uploads a
fresh per-emulator .hash: SHA-256 of "<total save bytes>|<newest save mtime ticks>". (EmuDeck uses
size alone; the Deck only compares for equality, so any change-sensitive value works.)

Called by D:\Emulation\tools\launchers\esde\EmulationStationDE.ps1 after ES-DE exits.
-DryRun lists what would change without uploading.
#>
param(
  [datetime]$Since = (Get-Date).AddHours(-12),
  [string]$Remote = 'Emudeck-GDrive',
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$saves  = 'D:\Emulation\saves'
$rclone = 'D:\Emulation\tools\rclone\rclone.exe'
$conf   = 'D:\Emulation\tools\rclone\rclone.conf'
$lock   = Join-Path $env:APPDATA 'EmuDeck\cloud.lock'

$logDir = Join-Path $env:LOCALAPPDATA 'EmuDeckHashSync'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'sync.log'
function Note($m){ $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m); $line | Out-File -FilePath $log -Append -Encoding utf8; $line }

# Same excludes EmuDeck uses for its own uploads (keys never leave the PC).
$excludes = '--exclude=/.fail_upload', '--exclude=/BigPEmuConfig.bigpcfg', '--exclude=/.fail_download',
            '--exclude=/system/prod.keys', '--exclude=/system/title.keys', '--exclude=/.pending_upload',
            '--exclude=/.watching', '--exclude=/*.lnk', '--exclude=/.cloud', '--exclude=/.emulator', '--exclude=/.user'

if (-not (Test-Path $rclone) -or -not (Test-Path $conf)) { Note "SKIP: rclone or its config not found."; exit 0 }

# Emulator folders with a real save file (not a dot-file) modified since the session started.
$changed = @(Get-ChildItem $saves -Directory | Where-Object {
  @(Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike '.*' -and $_.LastWriteTime -gt $Since }).Count -gt 0 })
if ($changed.Count -eq 0) { Note "Nothing changed since $($Since.ToString('yyyy-MM-dd HH:mm')); done."; exit 0 }
Note ("Changed since {0}: {1}" -f $Since.ToString('yyyy-MM-dd HH:mm'), (($changed | ForEach-Object Name) -join ', '))
if ($DryRun) { Note 'DRY RUN - nothing uploaded.'; exit 0 }

# Let any in-flight EmuDeck upload finish first (up to 3 minutes).
$wait = 0
while ((Test-Path $lock) -and $wait -lt 180) { Start-Sleep -Seconds 5; $wait += 5 }
if (Test-Path $lock) { Note 'WARNING: EmuDeck cloud.lock still present after 3 min; continuing.' }

# Cloud reachable?
# (no stderr redirection: in PS 5.1, redirecting a native command's stderr with ErrorAction Stop throws)
$null = & $rclone lsf "${Remote}:Emudeck/saves" --max-depth 1 --config $conf -q
if ($LASTEXITCODE -ne 0) { Note "SKIP: cloud not reachable (rclone exit $LASTEXITCODE)."; exit 0 }

$sha = [Security.Cryptography.SHA256]::Create()
foreach ($emu in $changed) {
  $dir = $emu.FullName; $name = $emu.Name
  & $rclone copy $dir "${Remote}:Emudeck/saves/$name/" --config $conf --fast-list --update --tpslimit 12 --checkers=50 @excludes -q
  if ($LASTEXITCODE -ne 0) { Note "FAILED: upload of '$name' (rclone exit $LASTEXITCODE); hash left unchanged."; continue }

  # EmuDeck hashes only the folder's total size, which misses same-size rewrites (a GB .srm is always
  # 8192 bytes). The Deck only checks whether the value changed, so also fold in the newest save's mtime.
  $files  = @(Get-ChildItem $dir -Recurse -File -Force | Where-Object { $_.Name -notlike '.*' })
  $size   = ($files | Measure-Object -Property Length -Sum).Sum; if ($null -eq $size) { $size = 0 }
  $newest = ($files | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
  $basis  = '{0}|{1}' -f $size, $(if ($newest) { $newest.Ticks } else { 0 })
  $hash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($basis))) -replace '-').ToLower()
  $hashFile = Join-Path $dir '.hash'
  [IO.File]::WriteAllText($hashFile, $hash + "`n", (New-Object Text.UTF8Encoding $false))
  & $rclone copyto $hashFile "${Remote}:Emudeck/saves/$name/.hash" --config $conf -q
  if ($LASTEXITCODE -eq 0) { Note "OK: '$name' uploaded; cloud .hash -> $($hash.Substring(0,12))..." }
  else { Note "FAILED: .hash upload for '$name' (rclone exit $LASTEXITCODE)." }
}
exit 0
