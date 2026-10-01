<#
Backup-KaijaSheet.ps1
Weekly snapshot of the Kaija character sheet to the NAS, dated the previous Wednesday.

Copies C:\Users\jjmorse\Documents\Kaija.pdf to
\\KrynnVault\Books\Spell and Blade\Character sheets\Kaija YYYY.MM.DD.pdf
ONLY if its contents (SHA-256) differ from the newest Kaija*.pdf already there.
Never overwrites an existing file. Run by the "Kaija Sheet Backup" task
(Thursdays 09:00, see Register-KaijaSheetBackup.ps1). -DryRun reports only.
#>
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$src    = 'C:\Users\jjmorse\Documents\Kaija.pdf'
$dstDir = '\\KrynnVault\Books\Spell and Blade\Character sheets'

$logDir = Join-Path $env:LOCALAPPDATA 'KaijaSheetBackup'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'backup.log'
# Out-File -Encoding utf8, not Tee-Object: PS 5.1's Tee -Append writes UTF-16 and garbles a mixed log.
function Note($m){ $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m); $line | Out-File -FilePath $log -Append -Encoding utf8; $line }

if ($DryRun) { Note 'DRY RUN - nothing will be copied.' }

# Most recent Wednesday strictly before today (Thursday run -> yesterday).
$today = (Get-Date).Date
$back  = ([int]$today.DayOfWeek - [int][DayOfWeek]::Wednesday + 7) % 7
if ($back -eq 0) { $back = 7 }
$stamp  = $today.AddDays(-$back).ToString('yyyy.MM.dd')
$target = Join-Path $dstDir ("Kaija $stamp.pdf")

# SAFETY 1: local sheet must exist.
if (-not (Test-Path -LiteralPath $src)) { Note "ABORT: source not found: $src"; exit 1 }

# SAFETY 2: NAS must be reachable. Retry for up to 8 h (asleep, away, NordVPN, NAS offline);
# Task Scheduler's restart-on-failure does not trigger on a non-zero exit code.
$tries = 0
while (-not (Test-Path -LiteralPath $dstDir)) {
  $tries++
  if ($tries -gt 32) { Note "ABORT: NAS unreachable after 8 h ($dstDir)."; exit 1 }
  Note "NAS unreachable ($dstDir); retry $tries/32 in 15 min."
  Start-Sleep -Seconds 900
}

$srcHash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash

# Newest existing sheet: latest YYYY.MM.DD in the name, falling back to LastWriteTime.
$newest = Get-ChildItem -LiteralPath $dstDir -Filter 'Kaija*.pdf' -File |
  Sort-Object @{ Expression = { if ($_.Name -match '(\d{4})\.(\d{2})\.(\d{2})') { [datetime]::new([int]$matches[1], [int]$matches[2], [int]$matches[3]) } else { $_.LastWriteTime } } } -Descending |
  Select-Object -First 1

if ($newest -and (Get-FileHash -LiteralPath $newest.FullName -Algorithm SHA256).Hash -eq $srcHash) {
  Note "UNCHANGED: Kaija.pdf matches newest NAS copy '$($newest.Name)'; nothing to do."
  exit 0
}

if (Test-Path -LiteralPath $target) {
  Note "SKIP: '$target' already exists with different contents; not overwriting."
  exit 0
}

$prev = if ($newest) { $newest.Name } else { '(none)' }
if ($DryRun) { Note "WOULD COPY: Kaija.pdf -> '$target' (changed since '$prev')."; exit 0 }

Copy-Item -LiteralPath $src -Destination $target
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $srcHash) {
  Note "FAILED: hash mismatch after copy to '$target'."
  exit 1
}
Note "COPIED: Kaija.pdf -> '$target' (changed since '$prev'), hash verified."
exit 0
