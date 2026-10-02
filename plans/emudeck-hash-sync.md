# EmuDeck per-emulator hash sync (Windows -> Steam Deck)

## Problem

Saves played on the Z13 reach Google Drive but never download on the Steam Deck. The Deck's per-emulator download compares the cloud `Emudeck/saves/<emu>/.hash` with its local copy and skips when they match ("Saves up to date"). On Windows, ES-DE sessions set `saves\.emulator` to `\`, so EmuDeck's watcher uploads the whole saves folder and `cloud_sync_save_hash` refreshes only the top-level `saves\.hash`. Per-emulator `.hash` files are never refreshed, so the cloud keeps the Deck's own value and the Deck never downloads. EmuDeck's launcher runs `git reset --hard` on its backend every launch, so patching EmuDeck itself would not stick.

## Fix

`Sync-EmuDeckHashes.ps1`, called by the (already locally customised) ES-DE launcher after ES-DE exits:

1. Wait for any in-flight EmuDeck upload (`%APPDATA%\EmuDeck\cloud.lock`) to clear, up to 3 minutes.
2. For each emulator folder under `D:\Emulation\saves` with a save file modified since the session started (dot-files ignored):
   - upload that folder with EmuDeck's own `rclone copy --update` flags and excludes, so the files are definitely in the cloud before the hash changes;
   - recompute the hash and write it to `<emu>\.hash`. EmuDeck's own method is SHA-256 of the folder's total byte size, which misses same-size rewrites (a Game Boy `.srm` is always 8192 bytes), so this uses SHA-256 of `<total save bytes>|<newest save mtime ticks>`. The Deck only compares the value for equality, so any change-sensitive value works;
   - `rclone copyto` that `.hash` to `Emudeck/saves/<emu>/.hash`.
3. Log to `%LOCALAPPDATA%\EmuDeckHashSync\sync.log`. `-DryRun` lists what would change. No internet or missing rclone: log and exit.

Trigger: `Watch-ESDESessions.ps1`, a hidden logon task ("EmuDeck Hash Sync Watcher", registered by `Register-EmuDeckHashWatcher.ps1`), polls every 10 s for `ES-DE.exe`, remembers the session start, and when ES-DE has been gone for 15 s (ES-DE sometimes restarts itself) runs `Sync-EmuDeckHashes.ps1 -Since <start - 1 min>`.

Rejected: hooking EmuDeck's ES-DE launcher. Its `launcherInit` -> `update_launchers` copies fresh launchers over `D:\Emulation\tools\launchers` on every launch and the backend is `git reset --hard` each time, so any edit there survives exactly one launch (this also undid the earlier windowed-mode default for the Steam route).

## Steps

1. Branch `emudeck-hash-sync` from main.
2. Write the script; dry-run it against today's changes.
3. Add `Watch-ESDESessions.ps1` + `Register-EmuDeckHashWatcher.ps1`; register and start the task.
4. Update README; commit; merge only with Josh's approval.
5. Draft an upstream issue for EmuDeck/emudeck-we for Josh to post.
