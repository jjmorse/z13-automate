#!/usr/bin/env bash
# Installs (or removes) the on-screen keyboard setup for your user. Run it as your normal user, never with sudo.
# Usage: bash install.sh [--dry-run] [--uninstall]   --dry-run prints each step and changes nothing
#        bash install.sh -h|--help   prints this help
set -euo pipefail

DRY_RUN=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,4p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "error: unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    echo "error: run this as your normal user, not as root or with sudo" >&2
    exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
SCRIPT_DST="$HOME/.local/bin/z13-onboard-follow"
AUTOSTART="$HOME/.config/autostart/onboard-autostart.desktop"
OVERRIDE='[Desktop Entry]
Type=Application
Name=Onboard
Hidden=true'

# Prints the command, and runs it unless this is a dry run.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}

# True if the autostart override exists and has exactly the content this installer writes.
override_matches() {
    [ -f "$AUTOSTART" ] && printf '%s\n' "$OVERRIDE" | cmp -s - "$AUTOSTART"
}

# Warns once if an Onboard process runs that z13-onboard.service does not manage.
warn_foreign_onboard() {
    local unit_pid pid
    unit_pid="$(systemctl --user show -p MainPID --value z13-onboard.service 2>/dev/null || true)"
    for pid in $({ pgrep -x onboard; pgrep -f /usr/bin/onboard; } 2>/dev/null | sort -u || true); do
        if [ "$pid" != "$unit_pid" ]; then
            echo "warning: an Onboard is already running; close it first or log out and back in"
            break
        fi
    done
    return 0
}

if [ "$UNINSTALL" -eq 1 ]; then
    run systemctl --user disable --now z13-onboard-follow.service z13-onboard.service || true
    run rm -f "$UNIT_DIR/z13-onboard.service" "$UNIT_DIR/z13-onboard-follow.service" "$SCRIPT_DST"
    if override_matches; then
        run rm -f "$AUTOSTART"
    else
        echo "autostart override left alone: it is missing or has different content"
    fi
    run systemctl --user daemon-reload
    echo "gsettings values were left as they are. To reset one: gsettings reset org.onboard.auto-show enabled"
    echo "Without the override, Onboard's package autostart applies again at your next login."
    exit 0
fi

if [ ! -x /usr/bin/onboard ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "warning: /usr/bin/onboard not found; install it first: sudo pacman -S --needed onboard"
    else
        echo "error: /usr/bin/onboard not found; install it first: sudo pacman -S --needed onboard" >&2
        exit 1
    fi
fi
if [ "$DRY_RUN" -eq 0 ]; then
    warn_foreign_onboard
fi

run install -Dm755 "$HERE/z13-onboard-follow.py" "$SCRIPT_DST"
run install -Dm644 "$HERE/z13-onboard.service" "$UNIT_DIR/z13-onboard.service"
run install -Dm644 "$HERE/z13-onboard-follow.service" "$UNIT_DIR/z13-onboard-follow.service"

if override_matches; then
    echo "autostart override already in place"
elif [ "$DRY_RUN" -eq 1 ]; then
    echo "would write $AUTOSTART"
else
    mkdir -p "$(dirname "$AUTOSTART")"
    printf '%s\n' "$OVERRIDE" > "$AUTOSTART"
    echo "+ wrote $AUTOSTART"
fi

run gsettings set org.onboard.keyboard input-event-source GTK
run gsettings set org.onboard.keyboard key-synth uinput
run gsettings set org.onboard.auto-show enabled true
run gsettings set org.gnome.desktop.interface toolkit-accessibility true

run systemctl --user daemon-reload
run systemctl --user enable --now z13-onboard.service z13-onboard-follow.service

echo "Check with: ~/.local/bin/z13-onboard-follow --status"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry run: nothing was changed."
fi
