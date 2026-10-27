#!/usr/bin/env bash
# Installs (or removes) the swipe-up keyboard KWin script for your user. Run it as your normal user, never with sudo.
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
PKG_ID="z13swipekeyboard"
ENABLE_KEY="${PKG_ID}Enabled"
INSTALLED_DIR="$HOME/.local/share/kwin/scripts/$PKG_ID"

# Prints the command, and runs it unless this is a dry run.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}

# Like run, but skips the step with a warning when the tool is missing (a dry run still prints it).
run_tool() {
    if [ "$DRY_RUN" -eq 0 ] && ! command -v "$1" >/dev/null 2>&1; then
        echo "warning: $1 is not installed, so this step was skipped: $*"
        return 0
    fi
    run "$@"
}

# Missing KDE tools only warn: the steps that need them are skipped.
for tool in kpackagetool6 kwriteconfig6 qdbus6; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "warning: $tool not found; the step that uses it will be skipped"
    fi
done

if [ "$UNINSTALL" -eq 1 ]; then
    run_tool kwriteconfig6 --file kwinrc --group Plugins --key "$ENABLE_KEY" false
    run_tool qdbus6 org.kde.KWin /KWin reconfigure
    run_tool kpackagetool6 --type=KWin/Script --remove "$PKG_ID" || true
    echo "Nothing else was changed."
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "Dry run: nothing was changed."
    fi
    exit 0
fi

if [ -d "$INSTALLED_DIR" ]; then
    ACTION="--upgrade"
else
    ACTION="--install"
fi

run_tool kpackagetool6 --type=KWin/Script "$ACTION" "$HERE"
run_tool kwriteconfig6 --file kwinrc --group Plugins --key "$ENABLE_KEY" true
run_tool qdbus6 org.kde.KWin /KWin reconfigure

echo "Check with: kpackagetool6 --type=KWin/Script --list"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry run: nothing was changed."
fi
