#!/usr/bin/env bash
# Installs (or removes) the Z13 Fn lock service. Needs root, except with --dry-run.
# Usage: sudo bash install.sh [--dry-run] [--uninstall]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            sed -n '2,3p' "${BASH_SOURCE[0]}" | sed 's/^# //'
            exit 0
            ;;
        *)
            echo "error: unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    echo "error: run with sudo (or use --dry-run to preview)" >&2
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

if [ "$UNINSTALL" -eq 1 ]; then
    run systemctl disable --now z13-fnlock.service z13-fnlock-resume.service || true
    run rm -f /usr/local/bin/z13-fnlock /etc/systemd/system/z13-fnlock.service /etc/systemd/system/z13-fnlock-resume.service
    run systemctl daemon-reload
    if [ "$DRY_RUN" -eq 0 ]; then
        echo "Removed. The keyboard may keep the last Fn lock state it was given (how long is not known)."
    fi
    exit 0
fi

for f in z13-fnlock.py z13-fnlock.service z13-fnlock-resume.service; do
    if [ ! -f "$HERE/$f" ]; then
        echo "error: missing $HERE/$f" >&2
        exit 1
    fi
done

run install -Dm755 "$HERE/z13-fnlock.py" /usr/local/bin/z13-fnlock
run install -Dm644 "$HERE/z13-fnlock.service" /etc/systemd/system/z13-fnlock.service
run install -Dm644 "$HERE/z13-fnlock-resume.service" /etc/systemd/system/z13-fnlock-resume.service
run systemctl daemon-reload
run systemctl enable z13-fnlock.service z13-fnlock-resume.service
run systemctl restart z13-fnlock.service

if [ "$DRY_RUN" -eq 0 ]; then
    sleep 1
    systemctl --no-pager --lines=8 status z13-fnlock.service || true
fi
