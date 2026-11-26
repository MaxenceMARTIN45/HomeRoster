#!/usr/bin/env bash
# =====================================================================================================
#  `update`: install the latest HomeRoster from GitHub, keep every setting, roll back if it fails.
#  Installed as /usr/local/bin/update by scripts/proxmox/install.sh.
#
#  Untouched: /etc/homeroster (homeroster.yaml, .env) and /var/lib/homeroster (database, services.yaml).
#  The database schema is upgraded by HomeRoster itself when it starts.
# =====================================================================================================
set -euo pipefail
APP=/opt/homeroster
BRANCH=$(git -C "$APP" rev-parse --abbrev-ref HEAD 2>/dev/null || echo develop)
[ "$BRANCH" = HEAD ] && BRANCH=develop
G=$'\e[32m'; R=$'\e[31m'; N=$'\e[0m'
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

healthy() {
  for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}
install_version() {
  git -C "$APP" reset -q --hard "$1"
  "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
  install -m 755 "$APP/scripts/proxmox/update.sh" /usr/local/bin/update
  systemctl restart homeroster
}

OLD=$(git -C "$APP" rev-parse HEAD)
git -C "$APP" fetch -q --depth 1 origin "$BRANCH"
NEW=$(git -C "$APP" rev-parse FETCH_HEAD)
if [ "$OLD" = "$NEW" ]; then
  echo "${G}✓${N} HomeRoster is up to date ($(git -C "$APP" rev-parse --short HEAD))."
  exit 0
fi

echo "Updating HomeRoster ${OLD:0:7} -> ${NEW:0:7}"
cp -a /var/lib/homeroster/homeroster.db "/var/lib/homeroster/homeroster.db.before-update" 2>/dev/null || true
install_version "$NEW"
if healthy; then
  echo "${G}✓${N} HomeRoster ${NEW:0:7} is running. Your settings were kept."
else
  echo "${R}✗${N} The new version does not start: going back to ${OLD:0:7}." >&2
  journalctl -u homeroster -n 30 --no-pager >&2 || true
  git -C "$APP" fetch -q --depth 1 origin "$OLD" 2>/dev/null || true
  systemctl stop homeroster
  cp -a /var/lib/homeroster/homeroster.db.before-update /var/lib/homeroster/homeroster.db 2>/dev/null || true
  install_version "$OLD"
  healthy && echo "Restored ${OLD:0:7}." || echo "Could not restore: see journalctl -u homeroster." >&2
  exit 1
fi
