# Mute the built-in Realtek speakers while docked

## Goal

When the Z13 is plugged into the CalDigit TS3 Plus dock, mute the built-in speakers (the "Speakers (Realtek(R) Audio)" playback endpoint). When it is undocked, unmute them. The tray app (`Z13Tray.ps1`) does this automatically.

## Design

- **Dock signal:** a present PnP device whose name matches `DockDeviceMatch` (default `TS3 Plus`, which matches "Thunderbolt 3(TM) Router, CalDigit. Inc. - TS3 Plus"). This is "the dock is connected", independent of whether the monitors are on. It is not the display count, which the existing refresh logic uses and which also flips for a travel monitor.
- **Mute method:** the Windows Core Audio endpoint mute (`IAudioEndpointVolume.SetMute`) on the active render endpoint whose friendly name contains `SpeakerEndpointMatch` (default `Realtek`). The volume level is not changed, so the speakers come back at the same volume.
- **Edge-triggered, not level-triggered:** act only when the docked state changes, plus once at tray start. If you unmute the speakers by hand while docked they stay unmuted until the next dock or undock.
- **Only undo what we did:** mute on dock only if the endpoint is not already muted, and record `MutedByUs` in a small state file. On undock, unmute only if `MutedByUs` is true. If you had the speakers muted yourself before docking, undocking leaves them muted.
- **State file:** `%LOCALAPPDATA%\Z13Automate\dock-audio-state.json` (`{ "Docked": bool, "MutedByUs": bool }`), so a tray restart or reboot does not lose the "we muted it" fact.
- **Config (`config.json`, read live):** `DockMuteSpeakers` (default true), `DockDeviceMatch`, `SpeakerEndpointMatch`. Missing keys fall back to those defaults.
- **Tray menu:** a checkable "Mute speakers when docked" item that flips `DockMuteSpeakers`, and the status line shows `docked` or `undocked`.
- **Where the code goes:** audio and dock functions in `Z13Display.psm1` (one implementation), called from the existing 5-second timer in `Z13Tray.ps1`. The scheduled task script is not involved.

## Steps

1. Branch `dock-mute-speakers` from main.
2. Add the functions to `Z13Display.psm1`: `Test-Z13Docked`, `Get-Z13SpeakerMute`, `Set-Z13SpeakerMute`, `Invoke-Z13DockAudio` (the edge logic and state file), exported from the module.
3. Hook `Invoke-Z13DockAudio` into the tray timer and the tray start-up, add the menu item and status text.
4. Add the three keys to `config.json` and document them in `README.md`.
5. Test without disturbing the live tray: parse-check, then exercise the functions in a throwaway PowerShell session, restoring the original mute state afterwards.
6. Commit on the branch. Merge and push only with Josh's approval. The running tray needs a restart to pick up the change.

## Rollback

Set `DockMuteSpeakers` to false in `config.json` (takes effect live), or check out `main` and restart the tray. If the speakers are left muted, unmute them in the Windows volume mixer or run `Set-Z13SpeakerMute -Muted $false`.

## Caveats

- The dock is matched by name, so a different dock needs a different `DockDeviceMatch`.
- Muting the endpoint changes only that device. Headphones, the CalDigit audio and the monitor audio are separate endpoints and are not touched.
- Detection polls PnP on the tray's 5-second timer, so a dock or undock takes up to about 5 seconds to act on. The cost of the check is measured and kept low.
