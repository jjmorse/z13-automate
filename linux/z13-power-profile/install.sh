#!/usr/bin/env bash
# Installs (or removes) the power profile switch: tlp-pd replaces power-profiles-daemon, so KDE's Power Mode slider works again.
# Usage: sudo bash install.sh system [--dry-run] [--uninstall] [--default balanced|performance|power-saver]   tlp-pd and the TLP settings
#        bash install.sh user [--dry-run] [--uninstall]   restarts KDE PowerDevil so the popover rereads the state
set -euo pipefail

MODE="${1:-}"
[ "$#" -gt 0 ] && shift
DRY_RUN=0
UNINSTALL=0
DEFAULT=balanced
DEFAULT_SET=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --default)
            if [ "$#" -lt 2 ]; then
                echo "error: --default needs a value: balanced, performance or power-saver" >&2
                exit 2
            fi
            DEFAULT="$2"
            DEFAULT_SET=1
            shift
            ;;
        *) echo "error: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$MODE" in
    system|user) ;;
    -h|--help|"") sed -n '2,4p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: first argument must be 'system' or 'user'" >&2; exit 2 ;;
esac

if [ "$MODE" = "user" ] && [ "$DEFAULT_SET" -eq 1 ]; then
    echo "error: --default only applies to 'system'" >&2
    exit 2
fi
if [ "$MODE" = "system" ]; then
    case "$DEFAULT" in
        balanced) PROFILE=BAL ;;
        performance) PROFILE=PRF ;;
        power-saver) PROFILE=SAV ;;
        *) echo "error: --default must be balanced, performance or power-saver" >&2; exit 2 ;;
    esac
fi

if [ "$MODE" = "system" ] && [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    echo "error: 'system' needs sudo (or use --dry-run to preview)" >&2
    exit 1
fi
if [ "$MODE" = "user" ] && [ "$(id -u)" -eq 0 ]; then
    echo "error: run 'user' as your normal user, not with sudo" >&2
    exit 1
fi

# Prints the command, and runs it unless this is a dry run.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}

if [ "$MODE" = "system" ]; then
    CONF=/etc/tlp.d/10-profile-default.conf
    if [ "$UNINSTALL" -eq 1 ]; then
        run systemctl disable --now tlp-pd.service || true
        run rm -f "$CONF"
        run tlp start || true
        echo "To get power-profiles-daemon back: sudo pacman -S power-profiles-daemon (it replaces tlp-pd)"
        echo "Backups named 10-profile-default.conf.bak-* were left in /etc/tlp.d"
        exit 0
    fi

    if ! command -v pacman >/dev/null 2>&1; then
        echo "error: this installer is for Arch-based systems with pacman" >&2
        exit 1
    fi
    # No --noconfirm: pacman asks before it removes power-profiles-daemon, and you answer that prompt yourself.
    run pacman -S --needed tlp-pd

    # TLP_AUTO_SWITCH=0 is what makes the KDE slider appear; TLP_PROFILE_DEFAULT sets the profile at boot.
    if [ -e "$CONF" ]; then
        run cp -p "$CONF" "$CONF.bak-$(date +%Y%m%d-%H%M%S)"
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "would write $CONF with these two lines:"
        echo "TLP_AUTO_SWITCH=0"
        echo "TLP_PROFILE_DEFAULT=$PROFILE"
    else
        TMP="$(mktemp)"
        trap 'rm -f "$TMP"' EXIT
        printf 'TLP_AUTO_SWITCH=0\nTLP_PROFILE_DEFAULT=%s\n' "$PROFILE" > "$TMP"
        run install -Dm644 "$TMP" "$CONF"
    fi

    run systemctl enable --now tlp.service
    run systemctl enable --now tlp-pd.service
    # Must come after tlp-pd starts: TLP only tells tlp-pd that automatic switching is off when TLP starts,
    # and restarting tlp-pd later resets that to on.
    run tlp start

    if [ "$DRY_RUN" -eq 0 ]; then
        echo "--- current profile:"
        tlpctl get || true
        echo "--- BatteryAware should read 'b false' (automatic switching off):"
        busctl --system get-property org.freedesktop.UPower.PowerProfiles /org/freedesktop/UPower/PowerProfiles org.freedesktop.UPower.PowerProfiles BatteryAware || true
    fi
    echo "Next, as your normal user (not sudo): bash install.sh user"
else
    if ! systemctl --user cat plasma-powerdevil.service >/dev/null 2>&1; then
        echo "KDE PowerDevil was not found; log out and back in so your desktop rereads the state"
        exit 0
    fi
    # tlp-pd does not tell KDE about the change, so restart PowerDevil to make the popover reread it.
    run systemctl --user restart plasma-powerdevil.service
    if [ "$UNINSTALL" -eq 1 ]; then
        echo "Open the Power & Battery popover in the system tray: the Power Mode slider should be gone. If it still shows one, log out and back in."
    else
        echo "Open the Power & Battery popover in the system tray: the Power Mode slider should be there. If it still says Automatic, log out and back in."
    fi
fi
