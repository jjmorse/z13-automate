# Linux folder and the Fn lock service

## Problem

This repo is where Josh shares the Z13 scripts with others, and so far everything in it is Windows-only. The Z13 now also runs CachyOS, where Fn+Esc does nothing: the folio keyboard sends `KEY_FN_ESC` but the kernel's `hid-asus` driver does not toggle Fn lock for it. A Linux service for this exists in the private `z13-tools` repo (`tools/z13-fnlock/`).

## Fix

- Add `linux/z13-fnlock/` with the same code as the `z13-tools` copy: the daemon (`z13-fnlock.py`), the two systemd units, `install.sh` (with `--dry-run` and `--uninstall`) and the unit tests.
- A README written for the public repo: it explains the issue and the one-line upstream kernel fix inline, and does not point at private paths.
- A short Linux section in the main README, and a note that everything outside `linux/` is Windows-only.
- Keep the two copies identical. `README.md` is the only file that differs on purpose. Check with `diff -r -x README.md -x __pycache__ ~/git/z13-tools/tools/z13-fnlock ~/git/z13-automate/linux/z13-fnlock`.

## Deferred: split the repo into windows/ and linux/

Moving the Windows scripts into `windows/` is a separate change and needs a deliberate step on the Windows machine:

- The scripts find each other through `$PSScriptRoot`, so moving them all together keeps them working against each other.
- `Install.ps1` and the `Register-*.ps1` scripts store absolute script paths in the Startup shortcut and in the scheduled tasks (Display Power Profile, Kaija Sheet Backup, EmuDeck Hash Sync Watcher, Speaker Amp Autofix, Sync Calibre). After a move those would point at files that no longer exist until Install.ps1 and the Register scripts are run again from the new location, elevated.
- The README, the plans, `config.json` handling and the private notes and changelog all name files at the repo root.
- Decide that when the Windows machine is at hand: move with `git mv` so history follows, re-run the installers, update the docs, and test the tray and each task.

## Steps

1. Branch `linux-fnlock` from main.
2. Copy the code, write the public README, add the main README section and this plan.
3. Run the unit tests and the installer dry run from the new location.
4. Commit; merge only with Josh's approval. The service itself is installed from the `z13-tools` copy for now.
