# z13-power-profile

On a Linux install that uses TLP for power management, KDE Plasma's Power & Battery popover has no Power Mode slider, because the slider talks to the `power-profiles-daemon` D-Bus service and that service conflicts with TLP. This folder installs `tlp-pd`, the replacement that TLP 1.9 and later ship, and turns off TLP's automatic profile switching so the slider appears. It is tested on an ASUS ROG Flow Z13 (GZ302EA) with CachyOS, KDE Plasma 6.7, TLP 1.10.2 and tlp-pd 1.10.2, and on that setup only.

## What was wrong

With no `power-profiles-daemon` service on the bus, the popover has no switch to show. On the Z13 under Linux, the firmware profile also stays `quiet` on AC power when nothing sets it, because TLP's `PLATFORM_PROFILE_ON_AC` line is commented out by default. Measured on a local LLM (Qwen3-Coder 30B, Vulkan), the `performance` profile gave about 7% faster generation and about 38% faster prompt processing than `quiet`.

## What it installs

- `tlp-pd` replaces `power-profiles-daemon`. Pacman asks before it removes the old package, and you answer that prompt yourself.
- `/etc/tlp.d/10-profile-default.conf` holds two lines: `TLP_AUTO_SWITCH=0` and `TLP_PROFILE_DEFAULT` set to `BAL`, `PRF` or `SAV`. If that file already exists, it is first copied to a `.bak-` file with a timestamp in its name.
- Automatic switching is off because with it on, the popover shows "Automatic: the TLP service automatically controls power profiles" and hides the slider.
- The trade-off: nothing switches to Performance by itself when you plug in. Your slider choice sticks until you change it.
- The boot profile is balanced unless you pass `--default performance` or `--default power-saver` to the system command.

## Install

Preview the system part first (no sudo):

```bash
bash linux/z13-power-profile/install.sh system --dry-run
```

Install the system part (needs sudo):

```bash
sudo bash linux/z13-power-profile/install.sh system
```

Then install the user part, as your normal user (no sudo):

```bash
bash linux/z13-power-profile/install.sh user
```

## Profile mapping

These mappings were measured with `tlpctl` on the Z13.

| Profile | Firmware platform profile | CPU energy preference |
| --- | --- | --- |
| performance | `performance` | `balance_performance` |
| balanced | `balanced` | `balance_power` |
| power-saver | `balanced` | `power` |

Power-saver stays on the firmware `balanced` profile because TLP asks for `low-power`, and the ASUS firmware only offers `quiet`, `balanced` and `performance`. To make power-saver use `quiet`, add `PLATFORM_PROFILE_ON_SAV=quiet` to a new `.conf` file in `/etc/tlp.d/`, then run `sudo tlp start`.

## Command line

- `tlpctl get` prints the current profile.
- `tlpctl set performance`, `tlpctl set balanced` or `tlpctl set power-saver` changes it.
- `tlpctl list` lists the profiles.

To check the flag that controls automatic switching, run:

```bash
busctl --system get-property org.freedesktop.UPower.PowerProfiles /org/freedesktop/UPower/PowerProfiles org.freedesktop.UPower.PowerProfiles BatteryAware
```

It should print `b false`.

## Gotchas

1. Restarting `tlp-pd` resets the automatic-switching flag to on until `sudo tlp start` runs again. After any restart of `tlp-pd.service`, run `sudo tlp start`.
2. `tlp-pd` does not announce the flag change to KDE. Either restart KDE's PowerDevil (`systemctl --user restart plasma-powerdevil.service`) or log out and back in before the slider appears. The user install step does the restart.

## Uninstall

```bash
sudo bash linux/z13-power-profile/install.sh system --uninstall
```

```bash
bash linux/z13-power-profile/install.sh user --uninstall
```

To get power-profiles-daemon back, run this (it replaces tlp-pd):

```bash
sudo pacman -S power-profiles-daemon
```

Backups named `10-profile-default.conf.bak-*` are left in `/etc/tlp.d`.

## Known limits

- Not yet checked through a reboot. TLP starts before `tlp-pd` at boot, so the "Automatic" text may come back after a restart. If it does, run `sudo tlp start` once.
- Tested on one machine only.
- Assumes pacman and KDE Plasma 6.
- The installer enables `tlp.service` but does not mask `systemd-rfkill`, which TLP's own documentation recommends.
- No test suite. The dry run is the check.
