#!/bin/sh
# Update the backend in /opt/hermes-tg-bridge from a GitHub archive, or roll it back.
#
#   sudo sh update-bridge.sh update [ref]          ref: branch, tag or commit (default: main)
#   sudo sh update-bridge.sh rollback <backup-dir>
#
# Nothing is deleted. The running tree, with its .venv, is moved aside as
# <APP>.bak-<stamp>; a failed tree is moved aside as <APP>.failed-<stamp>.
# Remove old copies by hand once the new version has proven itself.
# The Telegram session and state in /var/lib/hermes-tg-bridge are never touched.

set -eu

APP=${HERMES_TG_BRIDGE_APP:-/opt/hermes-tg-bridge}
REPO=https://github.com/OstaninKI/Hermes_tg_bridge
SERVICE=hermes-tg-bridge
STAMP=$(date +%Y%m%d-%H%M%S)

healthy() {
    sleep 5
    curl -fsS http://127.0.0.1:8765/health | grep -q '"status":"ok"'
}

rollback() {
    backup=$1
    [ -x "$backup/backend/.venv/bin/hermes-tg-bridge" ] || { echo "not a bridge backup: $backup" >&2; exit 2; }
    systemctl stop "$SERVICE" || true
    if [ -e "$APP" ]; then
        mv "$APP" "$APP.failed-$STAMP"
        echo "failed tree moved to $APP.failed-$STAMP"
    fi
    # The tree goes back to its original path, so its .venv works again.
    mv "$backup" "$APP"
    systemctl start "$SERVICE"
    if healthy; then
        echo "ROLLED_BACK to $backup"
    else
        echo "ROLLBACK_UNHEALTHY: check journalctl -u $SERVICE" >&2
        exit 1
    fi
}

update() {
    ref=${1:-main}
    tmp=$(mktemp -d)
    curl -fsSL "$REPO/archive/$ref.tar.gz" | tar -xz -C "$tmp"
    new=$(find "$tmp" -mindepth 1 -maxdepth 1 -type d)
    [ -f "$new/backend/pyproject.toml" ] || { echo "unexpected archive layout in $tmp" >&2; exit 2; }

    # Stage next to APP, so the swap below is a rename on one filesystem.
    staged="$APP.new-$STAMP"
    mv "$new" "$staged"
    rmdir "$tmp"
    chown -R root:root "$staged"

    backup="$APP.bak-$STAMP"
    systemctl stop "$SERVICE"
    mv "$APP" "$backup"
    mv "$staged" "$APP"
    echo "previous tree: $backup"

    if python3 -m venv "$APP/backend/.venv" \
        && "$APP/backend/.venv/bin/pip" install -q "$APP/backend[fast]" \
        && systemctl start "$SERVICE" \
        && healthy; then
        echo "UPDATED to $ref; rollback with: sudo sh $APP/deploy/update-bridge.sh rollback $backup"
    else
        echo "update failed, rolling back" >&2
        rollback "$backup"
        exit 1
    fi
}

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 2; }
case ${1:-} in
    update) update "${2:-main}" ;;
    rollback) [ -n "${2:-}" ] || { echo "usage: $0 rollback <backup-dir>" >&2; exit 2; }; rollback "$2" ;;
    *) echo "usage: $0 update [ref] | rollback <backup-dir>" >&2; exit 2 ;;
esac
