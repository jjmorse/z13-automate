# Keep Armoury Crate's lighting service from overriding G-Helper

## Problem

Josh sets the keyboard backlight in G-Helper (static bright green, brightness 3 of 3 on AC and battery) but the keyboard animates bright to dim instead. G-Helper's config is correct (`aura_mode` unset = Static, `keyboard_brightness_ac` = 3). The cause is Armoury Crate's `LightingService` (`C:\Program Files (x86)\LightingService\LightingService.exe`, startup type Automatic), which keeps its own Aura effect: `C:\ProgramData\ASUS\Armoury Crate Service\ArmouryCrate_v1.5.db` logged `{"Effect":"Rainbow","Type":"0"}` on every start (Oct 3 and 4), and the service re-applies it when it starts or the PC wakes. Armoury Crate and G-Helper both run, so each overwrites the other.

## Fix

Stop `LightingService` and set it to Disabled so only G-Helper controls the lights. This was done by hand on 2026-10-05 (elevated). Armoury Crate's Aura page stops working; GameVisual, Scenario Profiles and the rest of Armoury Crate are unaffected. G-Helper drives the keyboard directly over HID.

`Set-ArmouryLighting.ps1` makes the change repeatable and reversible:

- `-Disable` (default): stop and disable the service.
- `-Enable`: set it back to Automatic and start it (undo).
- `-Status`: report the service state without elevating.

Self-elevates like `Reset-Bluetooth.ps1`.

## Known risk

An Armoury Crate or ASUS update may re-enable the service. If the lights start animating again, run `.\Set-ArmouryLighting.ps1 -Status`; if it says Automatic or Running, run it again with `-Disable`.

## Steps

1. Branch `armoury-lighting-off` from main.
2. Write the script; test `-Status` and a parse check (the service is already disabled).
3. README entry; commit; merge only with Josh's approval.
