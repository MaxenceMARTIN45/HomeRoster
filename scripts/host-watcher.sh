#!/usr/bin/env bash
# =====================================================================================================
#  Reload a service when HomeRoster changes its files. Run it as root on the host that runs the service
#  (systemd required). HomeRoster lives in a read-only container and cannot reload anything itself.
#
#    WireGuard / PiVPN: apply wg0.conf changes with `wg syncconf` (no tunnel restart) and write the
#    latest handshakes every minute, so HomeRoster can show who is online:
#      bash scripts/host-watcher.sh wireguard [--interface wg0] [--dir /etc/wireguard]
#
#    nginx, SWAG, Caddy, HAProxy: reload the proxy when an allow-list file changes:
#      bash scripts/host-watcher.sh proxy --dir /etc/nginx/homeroster --reload "nginx -s reload"
#      bash scripts/host-watcher.sh proxy --dir /opt/caddy/homeroster --reload "docker exec caddy caddy reload --config /etc/caddy/Caddyfile"
#
#    Remove: bash scripts/host-watcher.sh remove
#
#  Both modes also let HomeRoster's container user (uid 10001) write to the directory (ACL).
# =====================================================================================================
set -euo pipefail

MODE=${1:-}; shift || true
INTERFACE=wg0; DIR=""; RELOAD=""
while [ $# -gt 0 ]; do
  case $1 in
    --interface) INTERFACE=$2; shift 2 ;;
    --dir) DIR=$2; shift 2 ;;
    --reload) RELOAD=$2; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
UNITS=/etc/systemd/system

allow_homeroster() {
  if command -v setfacl >/dev/null; then
    setfacl -m u:10001:rwx "$1" && setfacl -d -m u:10001:rwx "$1"
    find "$1" -maxdepth 2 -type f -exec setfacl -m u:10001:rw {} +
  else
    echo "! setfacl missing (apt install acl): give uid 10001 write access to $1 yourself"
  fi
}

case $MODE in
  wireguard)
    DIR=${DIR:-/etc/wireguard}
    CONF="$DIR/$INTERFACE.conf"
    [ -f "$CONF" ] || { echo "$CONF not found" >&2; exit 1; }
    cat > $UNITS/homeroster-wg.path <<EOF
[Unit]
Description=Apply HomeRoster changes to $CONF
[Path]
PathChanged=$CONF
[Install]
WantedBy=multi-user.target
EOF
    cat > $UNITS/homeroster-wg.service <<EOF
[Unit]
Description=wg syncconf $INTERFACE (HomeRoster)
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'wg syncconf $INTERFACE <(wg-quick strip $INTERFACE)'
EOF
    cat > $UNITS/homeroster-wg-status.service <<EOF
[Unit]
Description=Latest WireGuard handshakes for HomeRoster
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'wg show $INTERFACE latest-handshakes > $DIR/handshakes.tmp && mv $DIR/handshakes.tmp $DIR/handshakes'
EOF
    cat > $UNITS/homeroster-wg-status.timer <<EOF
[Unit]
Description=Every minute: WireGuard handshakes for HomeRoster
[Timer]
OnBootSec=30
OnUnitActiveSec=60
[Install]
WantedBy=timers.target
EOF
    allow_homeroster "$DIR"
    systemctl daemon-reload
    systemctl enable --now homeroster-wg.path homeroster-wg-status.timer
    echo "✓ $CONF is applied on every change; handshakes are written to $DIR/handshakes"
    ;;
  proxy)
    [ -n "$DIR" ] && [ -n "$RELOAD" ] || { echo "usage: proxy --dir <dir> --reload <command>" >&2; exit 1; }
    mkdir -p "$DIR"
    cat > $UNITS/homeroster-proxy.path <<EOF
[Unit]
Description=Reload the reverse proxy when HomeRoster changes $DIR
[Path]
PathChanged=$DIR
[Install]
WantedBy=multi-user.target
EOF
    cat > $UNITS/homeroster-proxy.service <<EOF
[Unit]
Description=Reload the reverse proxy (HomeRoster)
[Service]
Type=oneshot
ExecStart=/bin/sh -c '$RELOAD'
EOF
    allow_homeroster "$DIR"
    systemctl daemon-reload
    systemctl enable --now homeroster-proxy.path
    echo "✓ '$RELOAD' runs whenever a file changes in $DIR"
    ;;
  remove)
    systemctl disable --now homeroster-wg.path homeroster-wg-status.timer homeroster-proxy.path 2>/dev/null || true
    rm -f $UNITS/homeroster-wg.{path,service} $UNITS/homeroster-wg-status.{service,timer} $UNITS/homeroster-proxy.{path,service}
    systemctl daemon-reload
    echo "✓ removed"
    ;;
  *)
    sed -n '2,17p' "$0"; exit 1 ;;
esac
