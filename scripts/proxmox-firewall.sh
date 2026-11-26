#!/usr/bin/env bash
# =====================================================================================================
#  Optional helper for Proxmox VE: firewall rules for the container (LXC) running HomeRoster.
#  Run it ON THE PROXMOX HOST. Safe to run again: nothing is added twice, the firewall is backed up first.
#
#    bash proxmox-firewall.sh --ct <HomeRoster CT id> --ip <HomeRoster IP> --proxy-alias <alias> \
#         [--allow <target CT id>:<port>:<label>]...
#
#  Example:
#    bash proxmox-firewall.sh --ct 120 --ip 192.168.1.30 --proxy-alias reverseproxy \
#         --allow 101:10086:WGDashboard --allow 102:81:NPM --allow 103:8096:Jellyfin
#
#  Result:
#    - alias "homeroster" = <ip> in cluster.fw;
#    - <ct>.fw: inbound traffic dropped, except ping from the LAN and the reverse proxy on port 8000;
#    - each --allow target accepts HomeRoster on the given port (the services HomeRoster drives).
#  Requires an existing alias for the reverse proxy (--proxy-alias) and, for the ping rule, one named "lan".
# =====================================================================================================
set -euo pipefail
CT=""; IP=""; PROXY_ALIAS=""; ALLOW=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ct) CT=$2; shift 2 ;;
    --ip) IP=$2; shift 2 ;;
    --proxy-alias) PROXY_ALIAS=$2; shift 2 ;;
    --allow) ALLOW+=("$2"); shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done
[[ "$CT" =~ ^[0-9]+$ && "$IP" =~ ^[0-9.]+$ && -n "$PROXY_ALIAS" ]] || { sed -n '2,19p' "$0"; exit 1; }
command -v pct >/dev/null || { echo "Run this on the Proxmox host."; exit 1; }
pct config "$CT" >/dev/null || { echo "Container $CT does not exist."; exit 1; }

FW=/etc/pve/firewall
G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }

BACKUP="/root/pve-firewall.before-homeroster-$(date +%F-%H%M).tgz"
tar czf "$BACKUP" -C /etc/pve firewall
ok "Firewall backed up to $BACKUP"

if grep -qE "^homeroster " "$FW/cluster.fw"; then
  sed -i -E "s|^homeroster .*|homeroster $IP # CT $CT - HomeRoster|" "$FW/cluster.fw"
else
  sed -i "/^\[ALIASES\]/a homeroster $IP # CT $CT - HomeRoster" "$FW/cluster.fw"
fi
ok "Alias homeroster = $IP"

cat > "$FW/$CT.fw" <<EOF
[OPTIONS]
enable: 1
policy_in: DROP
dhcp: 1

[RULES]
IN Ping(ACCEPT) -source lan # ping from the LAN
IN ACCEPT -source $PROXY_ALIAS -p tcp -dport 8000 # reverse proxy -> HomeRoster
EOF
ok "$FW/$CT.fw: only the reverse proxy reaches port 8000"

for rule in "${ALLOW[@]}"; do
  IFS=: read -r target port label <<< "$rule"
  file="$FW/$target.fw"
  if [ ! -f "$file" ]; then warn "$file missing: rule for $label skipped"; continue; fi
  grep -qF -- "-source homeroster -p tcp -dport $port " "$file" ||
    echo "IN ACCEPT -source homeroster -p tcp -dport $port # HomeRoster -> $label" >> "$file"
  ok "$label (CT $target, port $port) accepts HomeRoster"
done

net0=$(pct config "$CT" | sed -n 's/^net0: //p')
case "$net0" in *firewall=1*) ;; *) pct set "$CT" -net0 "$net0,firewall=1"; ok "Firewall enabled on the CT $CT interface" ;; esac
if pve-firewall compile 2>&1 | grep -qiE 'error|unable|failed'; then
  echo "Firewall syntax error: restore the backup with 'tar xzf $BACKUP -C /etc/pve'"; exit 1
fi
ok "Firewall syntax checked"
