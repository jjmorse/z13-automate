#!/usr/bin/env bash
# Installs (or removes) the tablet-mode panel helper for your user. Run it as your normal user, never with sudo.
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
SCRIPT_DST="$HOME/.local/bin/z13-tablet-panel"
STATE_FILE="$HOME/.local/state/z13-tablet-panel/state.json"

# Prints the command, and runs it unless this is a dry run.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}

if [ "$UNINSTALL" -eq 1 ]; then
    # Stop first: the service puts the saved panel height back on SIGTERM.
    run systemctl --user stop z13-tablet-panel.service || true
    run systemctl --user disable z13-tablet-panel.service || true
    run rm -f "$UNIT_DIR/z13-tablet-panel.service" "$SCRIPT_DST"
    run systemctl --user daemon-reload
    if [ -e "$STATE_FILE" ]; then
        echo "warning: $STATE_FILE is still there, so the panel may still be tablet-sized (the laptop was not the only screen when the service stopped). Set the panel height back by hand in Plasma Edit Mode, then delete that file."
    fi
    echo "Nothing else was changed."
    exit 0
fi

# A missing PyGObject only warns here; the helper then exits with a message instead of starting.
if ! python3 -c 'from gi.repository import Gio' >/dev/null 2>&1; then
    echo "warning: PyGObject (gi) not found; the helper needs it. Install it with: sudo pacman -S --needed python-gobject"
fi

run install -Dm755 "$HERE/z13-tablet-panel.py" "$SCRIPT_DST"
run install -Dm644 "$HERE/z13-tablet-panel.service" "$UNIT_DIR/z13-tablet-panel.service"

run systemctl --user daemon-reload
run systemctl --user enable z13-tablet-panel.service
run systemctl --user restart z13-tablet-panel.service

echo "Check with: $SCRIPT_DST --status"
echo "Check with: journalctl --user -u z13-tablet-panel.service -n 20"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry run: nothing was changed."
fi
