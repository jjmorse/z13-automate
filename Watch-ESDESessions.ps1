<#
Watch-ESDESessions.ps1
Background watcher (started at logon by the "EmuDeck Hash Sync Watcher" task).
When an ES-DE session ends, runs Sync-EmuDeckHashes.ps1 -Since <session start> so the
Steam Deck downloads the saves made here. Lives outside EmuDeck on purpose: EmuDeck
overwrites its launchers (update_launchers) and resets its backend on every launch.
Polls every 10 s; negligible CPU.
#>
$ErrorActionPreference = 'Continue'
$sync = Join-Path $PSScriptRoot 'Sync-EmuDeckHashes.ps1'
$sessionStart = $null

while ($true) {
  $esde = Get-Process -Name 'ES-DE' -ErrorAction SilentlyContinue
  if ($esde -and -not $sessionStart) {
    # Session began: use the earliest ES-DE start time (covers a restart from inside ES-DE).
    $sessionStart = ($esde | Sort-Object StartTime | Select-Object -First 1).StartTime
  }
  elseif (-not $esde -and $sessionStart) {
    # Session ended. Wait for ES-DE restarts (it relaunches itself after some settings changes).
    Start-Sleep -Seconds 15
    if (-not (Get-Process -Name 'ES-DE' -ErrorAction SilentlyContinue)) {
      try { & $sync -Since $sessionStart.AddMinutes(-1) | Out-Null } catch { }
      $sessionStart = $null
    }
  }
  Start-Sleep -Seconds 10
}
