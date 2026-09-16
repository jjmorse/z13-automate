<#
Sync-CalibreLibrary.ps1
One-way mirror: local Calibre library (authoritative) -> NAS Calibre-Web library.

Local desktop Calibre is the ONLY writer. Do NOT manage books in Calibre-Web —
its changes to the library are OVERWRITTEN on the next sync. Trash (.caltrash) is
excluded on both sides so neither bin is touched.
#>
$ErrorActionPreference = 'Stop'
$src = 'C:\Users\jjmorse\Programs\Calibre'
$dst = '\\KrynnVault\docker\calibre\Calibre Library'

$logDir = Join-Path $env:LOCALAPPDATA 'CalibreSync'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'sync.log'
function Note($m){ $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m); $line | Tee-Object -FilePath $log -Append }

# SAFETY 1: local library must be intact -> prevents /MIR wiping the NAS if the local path is broken/empty.
if (-not (Test-Path -LiteralPath (Join-Path $src 'metadata.db'))) { Note "ABORT: local metadata.db not found at $src (refusing to mirror - would wipe NAS)."; exit 1 }
# SAFETY 2: NAS must be reachable (NordVPN up / NAS offline / Wi-Fi down).
if (-not (Test-Path -LiteralPath $dst)) { Note "ABORT: NAS destination unreachable ($dst). NordVPN on, or NAS offline?"; exit 1 }
# Warn if Calibre is open (metadata.db may be mid-write).
if (Get-Process calibre -ErrorAction SilentlyContinue) { Note "WARNING: desktop Calibre is running; metadata.db may be mid-write. Close it for a clean sync." }

Note "Mirror start: `"$src`" -> `"$dst`""
robocopy $src $dst /MIR /XD ".caltrash" /FFT /R:1 /W:2 /MT:16 /NP /NDL /NFL /TEE /LOG+:$log
$code = $LASTEXITCODE
Note ("robocopy exit code: $code  (0-7 = success, >=8 = failure)")
if ($code -ge 8) { Note "RESULT: FAILED"; exit $code }
Note "RESULT: OK"
exit 0
