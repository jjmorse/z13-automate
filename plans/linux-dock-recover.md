# Linux dock recovery tools

## Problem

After a long sleep with a Thunderbolt dock attached, one of the dock's USB controllers did not resume ("HC died"), which disconnected the devices on it. Replugging the dock fixed it, and the replug also made the kernel swap two DisplayPort connector names, so KDE applied saved monitor settings to the wrong monitors: a rotation was lost, two monitors landed at the same position, windows moved and a panel disappeared. Both took manual work to fix after every sleep.

## Fix

- `linux/z13-dock-recover/z13-dock-usb-revive.py` plus a systemd service that runs after resume. It finds dock USB controllers that report active but are not in power state `D0`, removes each and rescans its parent bridge.
- `linux/z13-dock-recover/z13-display-restore.py` plus a systemd user service. It identifies monitors by EDID instead of connector name, and re-applies a saved layout with `kscreen-doctor` after the monitor set or connector names change or the machine wakes. It sends only the settings that differ.
- An installer with `--dry-run` and `--uninstall`, unit tests with fake sysfs and monitor data, and a README.
- The code is the same as in the private `z13-tools` repo. The test data uses made-up serial numbers. Keep the two copies identical, except for the README, which is written for the public repo.

## Steps

1. Branch `linux-dock-recover` from main.
2. Copy the tool, write the public README, add the main README entries and this plan.
3. Run the unit tests from the new location and check the code is identical to the other copy.
4. Commit; merge only with Josh's approval.
