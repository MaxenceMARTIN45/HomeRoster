#!/usr/bin/env bash
# =====================================================================================================
#  HomeRoster in a Proxmox VE container, in one command (run it in the Proxmox shell, as root):
#
#    bash -c "$(curl -fsSL https://raw.githubusercontent.com/MaxenceMARTIN45/HomeRoster/develop/scripts/proxmox/homeroster-lxc.sh)"
#
#  Creates an unprivileged Debian 12 container, installs HomeRoster natively (Python, systemd, no Docker),
#  writes its configuration from your answers and prints the address and the setup token.
#  Later, inside the container, `update` installs the latest version and keeps every setting.
# =====================================================================================================
set -euo pipefail

REPO=${HOMEROSTER_REPO:-MaxenceMARTIN45/HomeRoster}
BRANCH=${HOMEROSTER_BRANCH:-develop}
RAW="https://raw.githubusercontent.com/$REPO/$BRANCH"
B=$'\e[1m'; G=$'\e[32m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'
ok()  { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
die() { printf '%s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

command -v pct >/dev/null || die "Run this on a Proxmox VE host (pct not found)."
[ "$(id -u)" = 0 ] || die "Run as root."
command -v whiptail >/dev/null || die "whiptail is missing (apt install whiptail)."

TITLE="HomeRoster"
ask()    { whiptail --title "$TITLE" --inputbox "$1" 10 72 "$2" 3>&1 1>&2 2>&3 || exit 1; }
menu()   { local q=$1 default=$2; shift 2; whiptail --title "$TITLE" --default-item "$default" --menu "$q" 22 72 12 "$@" 3>&1 1>&2 2>&3 || exit 1; }
checks() { local q=$1; shift; whiptail --title "$TITLE" --separate-output --checklist "$q" 22 72 12 "$@" 3>&1 1>&2 2>&3 || exit 1; }

# ------------------------------------------------------------------------------------- the container
CTID=$(ask "Container ID" "$(pvesh get /cluster/nextid)")
HOSTNAME_=$(ask "Hostname" "homeroster")
mapfile -t STORAGES < <(pvesm status -content rootdir | awk 'NR>1 {print $1}')
[ ${#STORAGES[@]} -gt 0 ] || die "No storage accepts containers (content type rootdir)."
STORAGE_ITEMS=(); for s in "${STORAGES[@]}"; do STORAGE_ITEMS+=("$s" ""); done
STORAGE=$(menu "Storage for the container disk" "${STORAGES[0]}" "${STORAGE_ITEMS[@]}")
DISK=$(ask "Disk size (GB)" "4")
CORES=$(ask "CPU cores" "1")
RAM=$(ask "Memory (MB)" "512")
BRIDGE=$(ask "Network bridge" "vmbr0")
NET=$(ask "IPv4 address with CIDR (e.g. 192.168.1.30/24), or dhcp" "dhcp")
GATEWAY=""
[ "$NET" = dhcp ] || GATEWAY=$(ask "Gateway" "$(ip route | awk '/default/ {print $3; exit}')")

# ------------------------------------------------------------------------------------- HomeRoster
LANGUAGE=$(menu "Interface and e-mail language" "en" en English fr Français es Español de Deutsch it Italiano \
  pt Português ru Русский zh 中文 hi हिन्दी ar العربية)
VPN=$(menu "Your VPN" "wg-easy" \
  wg-easy "WireGuard + wg-easy" wgdashboard "WireGuard + WGDashboard" tailscale "Tailscale" \
  headscale "Headscale" netbird "NetBird" zerotier "ZeroTier" wireguard "plain WireGuard (same host)" \
  pivpn "PiVPN (same host)")
PROXY=$(menu "Your reverse proxy" "npm" \
  npm "Nginx Proxy Manager" traefik "Traefik" caddy "Caddy" nginx "nginx" swag "SWAG" haproxy "HAProxy" zoraxy "Zoraxy")
APPS=$(checks "Apps you run (more can be added later in Settings > Services)" \
  jellyfin "Jellyfin" ON plex "Plex" OFF emby "Emby" OFF seerr "Seerr (Jellyfin or Emby)" ON \
  immich "Immich" ON openwebui "Open WebUI" OFF audiobookshelf "Audiobookshelf" OFF navidrome "Navidrome" OFF \
  vaultwarden "Vaultwarden" OFF homeassistant "Home Assistant" OFF nextcloud "Nextcloud" OFF | tr '\n' ' ')
OWNER=$(ask "Your first name (shown in the interface and the e-mails)" "Alex")
DOMAIN=$(ask "Domain of your apps (jellyfin.example.com -> example.com)" "example.com")
PROXY_IP=$(ask "LAN IP of your reverse proxy (the only one trusted for X-Real-IP)" "192.168.1.10")

# ------------------------------------------------------------------------------------- template
echo; echo "${B}Creating container $CTID${N}"
pveam update >/dev/null
TEMPLATE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)
[ -n "$TEMPLATE" ] || die "No Debian 12 template available."
TEMPLATE_STORAGE=$(pvesm status -content vztmpl | awk 'NR>1 {print $1; exit}')
pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
ok "template $TEMPLATE"

NETCONF="name=eth0,bridge=$BRIDGE,ip=$NET"
[ -n "$GATEWAY" ] && NETCONF="$NETCONF,gw=$GATEWAY"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" --hostname "$HOSTNAME_" --cores "$CORES" --memory "$RAM" \
  --swap 512 --rootfs "$STORAGE:$DISK" --net0 "$NETCONF" --unprivileged 1 --features nesting=1 --onboot 1 \
  --tags homeroster --description "HomeRoster: https://github.com/$REPO" >/dev/null
pct start "$CTID"
for _ in $(seq 1 30); do pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 && break; sleep 2; done
ok "container $CTID started"

# ------------------------------------------------------------------------------------- install
curl -fsSL "$RAW/scripts/proxmox/install.sh" -o /tmp/homeroster-install.sh
pct push "$CTID" /tmp/homeroster-install.sh /root/homeroster-install.sh
rm -f /tmp/homeroster-install.sh
pct exec "$CTID" -- env REPO="$REPO" BRANCH="$BRANCH" LANGUAGE="$LANGUAGE" VPN="$VPN" PROXY="$PROXY" APPS="$APPS" \
  OWNER="$OWNER" DOMAIN="$DOMAIN" PROXY_IP="$PROXY_IP" bash /root/homeroster-install.sh

IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
TOKEN=$(pct exec "$CTID" -- sh -c "grep '^HOMEROSTER_SETUP_TOKEN=' /opt/homeroster/.env | cut -d= -f2")
echo
echo "${B}${G}HomeRoster is running in container $CTID.${N}"
echo "  1. Point a proxy host of your reverse proxy (e.g. roster.$DOMAIN) to ${B}http://$IP:8000${N},"
echo "     restricted to your own VPN devices."
echo "  2. Open it, create the admin account with the setup token: ${B}$TOKEN${N}"
echo "  3. Settings > Services: enter the URLs and keys of your VPN, proxy and apps (each one is tested)."
echo "  ${D}Update later: run 'update' in the container (pct enter $CTID).${N}"
