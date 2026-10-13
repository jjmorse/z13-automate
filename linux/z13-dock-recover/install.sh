#!/usr/bin/env bash
# Installs (or removes) the dock recovery pieces.
# Usage: sudo bash install.sh system [--dry-run] [--uninstall]   the USB controller revive, runs after resume
#        bash install.sh user [--dry-run] [--uninstall]          the monitor layout restore, runs in your session
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
[ "$#" -gt 0 ] && shift
DRY_RUN=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        *) echo "error: unknown option: $arg" >&2; exit 2 ;;
    esac
done

case "$MODE" in
    system|user) ;;
    -h|--help|"") sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: first argument must be 'system' or 'user'" >&2; exit 2 ;;
esac

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
    SCRIPT=/usr/local/bin/z13-dock-usb-revive
    UNIT=/etc/systemd/system/z13-dock-usb-revive.service
    if [ "$UNINSTALL" -eq 1 ]; then
        run systemctl disable z13-dock-usb-revive.service || true
        run rm -f "$SCRIPT" "$UNIT"
        run systemctl daemon-reload
        exit 0
    fi
    run install -Dm755 "$HERE/z13-dock-usb-revive.py" "$SCRIPT"
    run install -Dm644 "$HERE/z13-dock-usb-revive.service" "$UNIT"
    run systemctl daemon-reload
    run systemctl enable z13-dock-usb-revive.service
    if [ "$DRY_RUN" -eq 0 ]; then
        echo "--- test run now (the dock should look healthy, so this changes nothing):"
        systemctl start z13-dock-usb-revive.service || true
        journalctl -u z13-dock-usb-revive.service --no-pager -n 6 -o cat || true
    fi
else
    SCRIPT="$HOME/.local/bin/z13-display-restore"
    UNIT="$HOME/.config/systemd/user/z13-display-restore.service"
    if [ "$UNINSTALL" -eq 1 ]; then
        run systemctl --user disable --now z13-display-restore.service || true
        run rm -f "$SCRIPT" "$UNIT"
        run systemctl --user daemon-reload
        echo "Your saved layout (~/.config/z13-display-restore/layout.json) was left in place."
        exit 0
    fi
    run install -Dm755 "$HERE/z13-display-restore.py" "$SCRIPT"
    run install -Dm644 "$HERE/z13-display-restore.service" "$UNIT"
    run systemctl --user daemon-reload
    run systemctl --user enable --now z13-display-restore.service
    echo "Next: when your monitors look the way you want, run:  z13-display-restore save"
fi
