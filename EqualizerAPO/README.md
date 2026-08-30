# Equalizer APO — Z13 speaker sound profile (auto per-device)

Gives the ROG Flow Z13's built-in speakers a loudness/tone boost (they're quiet and tinny otherwise) while leaving headphones completely untouched — so there's no manual profile switching when you put on the WH-1000XM4.

## How it works
- **Equalizer APO** (with the **Peace** GUI) is installed on the built-in **"Speakers (Realtek Audio)" ONLY** — chosen in the Equalizer APO Device Selector. It is deliberately NOT installed on the WH-1000XM4 or any other output.
- Because the APO only attaches to the speakers, the EQ applies to the speakers alone; headphones and everything else pass through raw, automatically. No `Device:`-scoping tricks needed, and no flaky Bluetooth EQ.
- It had previously been installed on the WH-1000XM4 too — that was the root cause of the old "boost ruins headphones, toggle it off" dance. Unchecking the headphones in the Device Selector removed it.
- `config.txt` contains `Include: peace.txt`; `peace.txt` holds the reddit-tuned Z13 speaker EQ (13 peaking filters: bass + treble lift, midrange dip).
- **Dolby is turned OFF for the speakers** (so it doesn't double-process on top of Equalizer APO) and **kept ON for headphones** (Atmos preserved).

## Files (backups of `C:\Program Files\EqualizerAPO\config\`)
- `config.txt` — the active include (EQ on).
- `peace.txt` — the applied EQ, in Equalizer APO syntax.
- `From reddit for Z13 speakers.peace` — the source profile in Peace's own format.

## Restore / rebuild on a fresh install
1. Install Equalizer APO (SourceForge). In the Device Selector, check **Speakers (Realtek Audio) only**, then reboot.
2. Copy these files into `C:\Program Files\EqualizerAPO\config\`.
3. The EQ applies live (Equalizer APO watches `config.txt`). Turn Dolby off for the speakers.

## Notes
- Peace does NOT autostart, so this config persists across reboots (nothing rewrites `config.txt`).
- The reddit EQ has large +12 dB boosts with no preamp. If it ever distorts at high volume, add a line `Preamp: -12 dB` at the top of `peace.txt`.
- Fully reversible: rerun the Equalizer APO Configurator to uninstall the APO per-device.
