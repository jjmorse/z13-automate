#!/usr/bin/env bash
# Installs (or removes) the virtual keyboard follower for your user. Run it as your normal user, never with sudo.
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
SCRIPT_DST="$HOME/.local/bin/z13-vkeyboard-follow"

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
    run systemctl --user disable --now z13-vkeyboard-follow.service || true
    run rm -f "$UNIT_DIR/z13-vkeyboard-follow.service" "$SCRIPT_DST"
    run systemctl --user daemon-reload
    echo "KWin's virtual keyboard mode was left as it is; it keeps the last value the watcher set."
    exit 0
fi

if ! /usr/bin/python3 -I -c 'from gi.repository import Gio' >/dev/null 2>&1; then
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "warning: PyGObject (gi) not found; install it first: sudo pacman -S --needed python-gobject"
    else
        echo "error: PyGObject (gi) not found; install it first: sudo pacman -S --needed python-gobject" >&2
        exit 1
    fi
fi

run install -Dm755 "$HERE/z13-vkeyboard-follow.py" "$SCRIPT_DST"
run install -Dm644 "$HERE/z13-vkeyboard-follow.service" "$UNIT_DIR/z13-vkeyboard-follow.service"

run systemctl --user daemon-reload
run systemctl --user enable --now z13-vkeyboard-follow.service

echo "Check with: journalctl --user -u z13-vkeyboard-follow.service -n 20"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry run: nothing was changed."
fi
