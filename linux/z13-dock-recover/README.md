# z13-dock-recover

Two small fixes so a Thunderbolt dock and its monitors come back on their own after sleep or a replug, with no manual work. Written for the ASUS ROG Flow Z13 (GZ302EA) with a CalDigit TS3 Plus dock, on CachyOS with KDE Plasma 6 on Wayland. It is tested on that one setup only; the USB part will need its root port addresses changed on other hardware (see below).

## What went wrong

After a long sleep with the dock attached:

1. One of the dock's USB controllers (a Fresco FL1100) did not come back. The kernel logged "Controller not ready at resume -19" and "HC died", and everything on that controller (keyboard, webcam, card reader) disconnected. The controller read power state `D3cold` while its runtime status said `active`. Replugging the dock fixed it.
2. When the dock came back, the kernel handed out two DisplayPort connector names in the opposite order. KDE restores saved monitor settings by connector name, so one monitor lost its rotation, two monitors landed at the same position, windows moved and a panel disappeared.

## The two pieces

- **`z13-dock-usb-revive`** (root, a systemd service that runs after resume). It finds USB controllers behind the dock's USB4 root ports that claim to be active but are not in power state `D0`, removes each one and rescans its parent bridge. That is what a replug does. It makes one attempt per controller and logs if the controller does not come back.
- **`z13-display-restore`** (your user session). It identifies each monitor by its EDID (manufacturer, product code and serial), not by connector name, and applies your saved layout to whichever connector each monitor is on now, using `kscreen-doctor`. It sends only the settings that differ, so monitors that are already right are not touched.

## Install

Preview, then install the system part (needs sudo):

```bash
bash linux/z13-dock-recover/install.sh system --dry-run
```

```bash
sudo bash linux/z13-dock-recover/install.sh system
```

Install the user part (no sudo):

```bash
bash linux/z13-dock-recover/install.sh user
```

Once your monitors are arranged the way you want them, save that arrangement:

```bash
~/.local/bin/z13-display-restore save
```

## Using it on other hardware

The USB part looks for controllers behind the USB4 root ports `0000:00:01.1` and `0000:00:01.2`, which are the Z13's. Find yours with `lspci -tv` (the ports your dock's PCIe devices hang off), then change `--ports` in `z13-dock-usb-revive.service` (add `--ports 0000:00:XX.X,0000:00:YY.Y` after `fix`) and run `z13-dock-usb-revive check` to confirm it sees your dock's USB controllers. The monitor part has no hardware-specific settings.

## How the monitor part behaves

- It does nothing until it has a saved layout, and then only acts on events: when the service starts, when the set of connected monitors or their connector names change, and after the machine wakes from sleep. It waits for things to settle for 5 seconds first, and at most applies once every 10 seconds.
- If you rearrange monitors on purpose, run `z13-display-restore save` again. Otherwise the next dock or wake event puts the saved layout back.
- If fewer than two of the saved monitors are connected (for example the laptop on its own), it leaves the layout alone.
- If a saved monitor is missing, the others are applied with their priorities renumbered.

Useful commands: `z13-display-restore status` shows each monitor, its identity, and any difference from the saved layout. `z13-display-restore apply --dry-run` prints the command it would run.

The layout is stored in `~/.config/z13-display-restore/layout.json`. Uninstalling leaves it in place.

## Uninstall

```bash
sudo bash linux/z13-dock-recover/install.sh system --uninstall
```

```bash
bash linux/z13-dock-recover/install.sh user --uninstall
```

## Tests

```bash
python3 -I -m unittest discover -v -s linux/z13-dock-recover -p 'test_*.py'
```

The tests use fake sysfs trees and fake monitor data, including the swapped-connector case. They need no hardware.

## Known limits

- **USB revive:** the service has now run after a real overnight sleep. Two of the dock's three USB controllers (Fresco FL1100) had died on resume with "Controller not ready at resume -19" and "HC died". The service removed each one and rescanned its parent bridge, but they did not come back within its 15 seconds, so it logged that a replug was needed. Replugging the dock fixed it. So the software replug does not reliably revive a dead controller.
- **Resume outcome varies:** on a later wake the PCIe link to the dock went down and came back up during resume (kernel log: `pciehp: Slot(0): Link Down`, then `Link Up`), and the controllers re-enumerated by themselves within about a second. So the outcome after resume varies between wakes.
- **Monitors after resume:** the monitor restore only helps once a monitor is detected. After some wakes a monitor on the dock is detected as a 640x480 ghost output (`EDID err: 2` in the kernel log) or not at all, until its DisplayPort cable is replugged at the dock. The tool cannot fix that.
- **Identical monitors:** two monitors of the same model with no serial number cannot be told apart.
- **What it restores:** mode, rotation, scale, position and priority. Not HDR, color profiles or per-monitor brightness.
- **Windows:** when monitors disappear and come back, KDE moves windows. This tool restores where monitors are, not where windows were.

## What was tried and did not help

- A udev rule forbidding the D3cold power state and runtime suspend for the dock's USB controllers and Thunderbolt bridges. The idea was that they dropped into D3cold and did not power up in time. It did not prevent the failure: the next wake showed the PCIe link dropping and re-forming at resume, with no D3cold involved. It was removed.
- Raising the Thunderbolt driver's DisplayPort receiver timeout (`thunderbolt` module parameter `dprx_timeout`, here 30000) did not stop the DisplayPort tunnels failing.

## Possible upstream fixes (none ready to apply)

No ready-made kernel patch is known for the s2idle (suspend-to-idle) resume failure. The links below are leads only.

- [thunderbolt: fix S4 (hibernate) resume incongruities](https://patchew.org/linux/20260106053749.61440-1-superm1@kernel.org): concerns hibernate resume only, and the reporter's problem was resolved a different way.
- [thunderbolt: re-add DP resources on resume](https://patchew.org/linux/20250917082456.1790252-1-lijiayi@kylinos.cn): an unmerged 2025 request-for-comments patch, tested on a different platform and an old kernel. It is relevant only because its symptom (DisplayPort tunnel not re-created after resume, "no suitable DP IN adapter available") resembles a dock monitor staying blank after resume.
- [Kernel bug 219748](https://bugzilla.kernel.org/show_bug.cgi?id=219748): a dock whose displays have EDID read failures with AMD GPUs.
- [amdgpu VPE idle timeout](https://lists.freedesktop.org/archives/amd-gfx/2025-August/129442.html): a Strix Halo resume freeze fix, a different symptom.

### Collecting a trace for a bug report

The next step is to turn on Thunderbolt driver debug logging before the next sleep, keep the kernel log from the resume, and report it to the kernel's Thunderbolt/USB4 maintainers. Turn the logging on with:

```bash
sudo sh -c "echo 'module thunderbolt +p' > /sys/kernel/debug/dynamic_debug/control"
```

The setting resets at reboot, and the debug messages then show up in `journalctl -k`.
