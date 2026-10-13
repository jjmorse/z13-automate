# Linux power profile slider

## Problem

On a Linux install that uses TLP for power management, KDE Plasma's Power & Battery popover has no profile switch. The switch talks to a power-profiles D-Bus service, `power-profiles-daemon`, and that service conflicts with TLP, so TLP users do not run it. On the Z13 this also leaves the firmware in its `quiet` profile on AC power, which costs about 7% in local-model generation speed and 38% in prompt processing speed compared with `performance`.

## Fix

- `linux/z13-power-profile/install.sh` installs `tlp-pd`, TLP's own implementation of the same D-Bus service, which replaces `power-profiles-daemon`. It writes `/etc/tlp.d/10-profile-default.conf` with `TLP_AUTO_SWITCH=0` and a boot profile, because with TLP's default automatic switching the popover only says "Automatic" and hides the slider. It enables the services, runs `tlp start` after `tlp-pd` is up (TLP reports the automatic-switching state to `tlp-pd` only then), and a `user` step restarts KDE's PowerDevil so the popover rereads that state.
- A README with the profile mapping measured on the Z13, the two gotchas and the limits.

## Steps

1. Branch `linux-power-profile` from main (done).
2. A subagent writes the installer and README; check them with `bash -n`, `shellcheck` if present, and `--dry-run` of both modes.
3. Add the main README entries and this plan.
4. Commit; merge only with Josh's approval.

## Not done

- Not tested through a reboot: at boot `tlp` starts before `tlp-pd`, so the slider may need a startup hook. If it does, add one and update the README.
- No test suite: the installer only runs system commands. The dry run is the check.
