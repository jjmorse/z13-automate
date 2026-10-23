# z13-claude-idle-sleep

This package suspends the machine after an hour without keyboard or mouse input, but only when Claude Desktop's keep-awake claims are the only thing blocking sleep. It is written for an ASUS ROG Flow Z13 (GZ302EA) running KDE Plasma 6.8 on Wayland; other setups are untested.

## What was wrong

Claude Desktop for Linux takes a five-minute keep-awake claim about every 30 minutes, even when nothing is happening. KDE shows it as a `sleep` inhibition from `/usr/bin/claude-desktop`. The claim is made during the app's credential rotation: the refresh is answered with a `get_device_info` tool call, which the keep-awake manager counts as activity. PowerDevil, KDE's power manager, resets its idle timers whenever an inhibition changes, and its suspend action does nothing while sleep is inhibited. So an idle-sleep timeout never fires while Claude Desktop is open with Remote Control on. The longest gap between claims is only about 22 to 27 minutes. The upstream report is at https://claudeissues.com/issue/89110-bug-claude-desktop-inhibits-sleep-in-linux-and-leaves-orphaned-processes-when-qu

## How it works

A user service watches keyboard and mouse input through `swayidle`, because KDE on Wayland does not report idle time any other way. When input has been idle for the timeout (default 3600 seconds), it suspends only if all of these hold:

- KDE lists at least one inhibition, and every one is from `/usr/bin/claude-desktop`. An empty list is left to PowerDevil. Any other app, such as a game or a video, blocks the helper.
- Claude Desktop's log (`~/.config/Claude/logs/main.log`) shows only rotation claims for the whole timeout. A Code turn, a manual hold, a remote tool call or any unrecognised keep-awake line counts as activity and postpones sleep.
- The log covers the whole timeout, and the machine has not come out of sleep in the last 10 minutes.

Anything it cannot read or does not recognise means it does not suspend. It suspends through PowerDevil's own Suspend call, so KDE's lock-on-resume behaviour still applies. It writes a journal line when its reason changes, and one for every suspend. After it asks for a suspend it waits three minutes before asking again, so a slow suspend is not requested twice.

## Requirements

It needs `swayidle`, which uses KWin's idle protocol, so it works only on KDE Plasma on Wayland. Install it first:

```bash
sudo pacman -S --needed swayidle
```

## Install

Preview the installer from the repository root. It changes nothing:

```bash
bash linux/z13-claude-idle-sleep/install.sh --dry-run
```

Install, as your normal user. The installer refuses to run as root:

```bash
bash linux/z13-claude-idle-sleep/install.sh
```

The installer copies the helper to `~/.local/bin/z13-claude-idle-sleep`, copies the unit to `~/.config/systemd/user/`, and starts (or restarts) the service now and enables it at login.

## Check it works

Print the inputs and the decision for the current moment. It assumes the input is idle and never suspends:

```bash
~/.local/bin/z13-claude-idle-sleep --explain
```

Run the helper in a terminal with a short timeout. With `--dry-run` it logs `would suspend` instead of suspending. It keeps running until you stop it with Ctrl+C:

```bash
~/.local/bin/z13-claude-idle-sleep --idle-seconds 300 --dry-run
```

Show the service's recent log lines:

```bash
journalctl --user -u z13-claude-idle-sleep.service -n 20
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--idle-seconds N` | 3600 | Seconds without input before a suspend is allowed |
| `--poll-seconds N` | 60 | Seconds between checks when no input event arrives |
| `--log PATH` | `~/.config/Claude/logs/main.log` | Claude Desktop log to read |
| `--dry-run` | off | Log `would suspend` instead of suspending |
| `--explain` | off | Print one decision and exit; never suspends |

To change the options the service uses, run `systemctl --user edit z13-claude-idle-sleep.service` and add these lines, then save and close the editor:

```ini
[Service]
ExecStart=
ExecStart=%h/.local/bin/z13-claude-idle-sleep --idle-seconds 1800
```

Restart the service so it reads the new command line:

```bash
systemctl --user restart z13-claude-idle-sleep.service
```

## Uninstall

Run this from the repository root:

```bash
bash linux/z13-claude-idle-sleep/install.sh --uninstall
```

The uninstall stops and disables the service, and removes the helper and the unit file. Nothing else was changed.

## Known limits

- It depends on the wording of Claude Desktop's log. If an app update changes those lines, the helper does nothing.
- Any other app's inhibition, such as a game or a video, also blocks it.
- It changes no settings in Claude Desktop or PowerDevil.
