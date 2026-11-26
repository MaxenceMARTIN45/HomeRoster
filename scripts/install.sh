#!/usr/bin/env bash
# =====================================================================================================
#  HomeRoster installer: run it on the Docker host (a VM, an LXC, a NAS...), from the repository root.
#
#    bash scripts/install.sh            first install, or add the secrets of newly configured apps
#    bash scripts/install.sh --update   rebuild and restart, keeping the configuration
#
#  1. Creates config/homeroster.yaml from the example: language, your VPN, your reverse proxy, the apps
#     you run, owner, domain and reverse proxy IP.
#  2. Asks for every secret referenced as ${VARIABLE} in that file and stores it in .env (mode 600).
#  3. Builds and starts the container, then tests the connection to every service.
# =====================================================================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

CONFIG=config/homeroster.yaml
EXAMPLE=config/homeroster.example.yaml
ENV_FILE=.env
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '%s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

command -v docker >/dev/null || die "Docker is not installed."
docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing (docker compose)."

env_get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/\$\$/$/g' || true; }
ask() {         # ask VAR "Question" "default"
  local var=$1 question=$2 default=${3:-} answer
  read -rp "  $question${default:+ [$default]}: " answer
  printf -v "$var" '%s' "${answer:-$default}"
}
choose() {      # choose VAR "Question" option1 option2 ...   (the first one is the default)
  local var=$1 question=$2 answer i; shift 2
  say "  $question"
  i=1; for option in "$@"; do say "    ${B}$i${N}) $option"; i=$((i + 1)); done
  while true; do
    read -rp "  Choice [1]: " answer; answer=${answer:-1}
    if [[ "$answer" =~ ^[0-9]+$ ]] && [ "$answer" -ge 1 ] && [ "$answer" -le $# ]; then
      printf -v "$var" '%s' "${!answer%% *}"; return
    fi
  done
}
ask_secret() {  # ask_secret VAR "Question" "current value"
  local var=$1 question=$2 current=${3:-} answer keep=""
  [ -n "$current" ] && keep=" ${D}[Enter = keep the current value]${N}"
  read -rsp "  $question$keep: " answer; echo
  printf -v "$var" '%s' "${answer:-$current}"
}

if [ "${1:-}" != "--update" ]; then
  say ""; say "${B}HomeRoster setup${N}  ${D}(Enter accepts the value in brackets)${N}"

  # ---------------------------------------------------------------- 1. configuration file
  if [ ! -f "$CONFIG" ]; then
    say ""; say "${B}1. Your homelab${N}"
    LANGS=$(ls src/homeroster/locales/*.json | xargs -n1 basename | sed 's/\.json$//' | tr '\n' ' ')
    ask LANGUAGE "Interface and e-mail language ($LANGS)" "en"
    [ -f "src/homeroster/locales/$LANGUAGE.json" ] || die "Unknown language: $LANGUAGE"
    say ""
    choose VPN "Which VPN do you use?" \
      "wgdashboard  (WireGuard + WGDashboard)" \
      "wg-easy      (WireGuard + wg-easy v15+)" \
      "wireguard    (plain WireGuard, wg0.conf on this host)" \
      "pivpn        (PiVPN with WireGuard, on this host)" \
      "tailscale    (Tailscale: friends are invited by e-mail)" \
      "headscale    (Headscale, self-hosted Tailscale server)" \
      "netbird      (NetBird, cloud or self-hosted)" \
      "zerotier     (ZeroTier, Central or a self-hosted controller)"
    say ""
    choose PROXY "Which reverse proxy do you use?" \
      "npm          (Nginx Proxy Manager)" \
      "traefik      (Traefik v2 or v3)" \
      "caddy        (Caddy)" \
      "nginx        (plain nginx)" \
      "swag         (SWAG by LinuxServer)" \
      "haproxy      (HAProxy)" \
      "zoraxy       (Zoraxy)"
    say ""
    ALL_APPS="jellyfin plex emby seerr immich openwebui audiobookshelf navidrome vaultwarden homeassistant nextcloud"
    say "  Which apps run on your server? (space-separated, Enter = jellyfin seerr immich openwebui)"
    say "    ${D}$ALL_APPS${N}"
    say "    ${D}More can be added later in HomeRoster: Settings > Services.${N}"
    while true; do
      read -rp "  Apps: " APPS; APPS=${APPS:-jellyfin seerr immich openwebui}
      bad=""; for a in $APPS; do [[ " $ALL_APPS " == *" $a "* ]] || bad="$bad $a"; done
      [ -z "$bad" ] && break
      warn "unknown:$bad"
    done
    say ""
    ask OWNER "Your first name, shown in the interface and the e-mails" "Alex"
    ask DOMAIN "Domain of your apps (jellyfin.example.com -> example.com)" "example.com"
    ask PROXY_IP "LAN IP of your reverse proxy (the only one trusted for X-Real-IP)" "192.168.1.10"
    mkdir -p config
    VPN=$VPN PROXY=$PROXY APPS=$APPS LANGUAGE=$LANGUAGE OWNER=$OWNER DOMAIN=$DOMAIN PROXY_IP=$PROXY_IP       bash scripts/make-config.sh "$EXAMPLE" "$CONFIG"

    # Some providers work with files on this host: mount their directory in the container.
    MOUNTS=()
    case $VPN in
      wireguard|pivpn)
        say ""; say "  HomeRoster edits wg0.conf directly. Install the watcher that applies the changes:"
        say "  ${B}sudo bash scripts/host-watcher.sh wireguard${N}"
        ask WG_DIR "WireGuard directory on this host" "/etc/wireguard"
        MOUNTS+=("$WG_DIR:/wireguard") ;;
    esac
    case $PROXY in
      traefik)
        say ""; say "  HomeRoster writes its middlewares to Traefik's dynamic configuration directory"
        say "  (the one loaded with --providers.file.directory)."
        ask PROXY_DIR "Path of that directory on this host" "/opt/traefik/dynamic"
        MOUNTS+=("$PROXY_DIR:/traefik") ;;
      caddy|nginx|swag|haproxy)
        say ""; say "  HomeRoster writes one allow-list file per site into a directory your proxy reads."
        ask PROXY_DIR "Path of that directory on this host" "/etc/$PROXY/homeroster"
        say "  Reload the proxy on every change: ${B}sudo bash scripts/host-watcher.sh proxy --dir $PROXY_DIR --reload \"<reload command>\"${N}"
        MOUNTS+=("$PROXY_DIR:/proxy") ;;
    esac
    if [ ${#MOUNTS[@]} -gt 0 ]; then
      { printf 'services:\n  homeroster:\n    volumes:\n'; printf '      - %s\n' "${MOUNTS[@]}"; } > docker-compose.override.yml
      for mount in "${MOUNTS[@]}"; do
        dir=${mount%%:*}; mkdir -p "$dir" 2>/dev/null || true
        [ "$(id -u)" = 0 ] && setfacl -m u:10001:rwx "$dir" 2>/dev/null || true
      done
      ok "docker-compose.override.yml created (uid 10001 must be able to write to the mounted directories)"
    fi
    ok "$CONFIG created"
    say ""
    say "  Now open ${B}$CONFIG${N} and set the internal URL of each service (VPN, reverse proxy, apps)."
    say "  Remove the apps you do not use, add others (see README > Adding an app)."
    read -rp "  Press Enter once the file is ready... " _
  else
    ok "$CONFIG found (kept as is)"
  fi

  # ---------------------------------------------------------------- 2. secrets
  say ""; say "${B}2. Secrets${N}  ${D}(stored in $ENV_FILE, readable by its owner only; typing is hidden)${N}"
  mapfile -t VARS < <(grep -v '^\s*#' "$CONFIG" | grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*\}' | tr -d '${}' | sort -u)
  declare -A VALUES
  for var in "${VARS[@]}"; do
    current=$(env_get "$var")
    if [[ "$var" =~ (KEY|PASSWORD|TOKEN|SECRET) ]]; then
      ask_secret value "$var" "$current"
    else
      ask value "$var" "$current"
    fi
    VALUES[$var]=$value
  done
  SETUP_TOKEN=$(env_get HOMEROSTER_SETUP_TOKEN)
  [ -n "$SETUP_TOKEN" ] || SETUP_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  TZ_VALUE=$(env_get TZ); TZ_VALUE=${TZ_VALUE:-$(cat /etc/timezone 2>/dev/null || echo UTC)}

  [ -f "$ENV_FILE" ] && cp "$ENV_FILE" "$ENV_FILE.bak-$(date +%F-%H%M)"
  umask 077
  {
    echo "# Written by scripts/install.sh. Never commit this file."
    echo "HOMEROSTER_SETUP_TOKEN=$SETUP_TOKEN"
    echo "TZ=$TZ_VALUE"
    for var in "${VARS[@]}"; do
      value=${VALUES[$var]// /}                 # app passwords are often displayed in groups of 4
      printf '%s=%s\n' "$var" "${value//\$/\$\$}" # Compose expands "$" in .env: double it
    done
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "Secrets saved to $ENV_FILE"
fi

[ -f "$CONFIG" ] && [ -f "$ENV_FILE" ] || die "Run 'bash scripts/install.sh' without --update first."

# ---------------------------------------------------------------- 3. build and start
say ""; say "${B}3. Build and start${N}"
mkdir -p data
if [ "$(id -u)" = 0 ]; then chown 10001:10001 data && chmod 700 data; else warn "not root: make sure ./data is writable by uid 10001"; fi
docker compose up -d --build
status="?"
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' homeroster 2>/dev/null || echo "?")
  [ "$status" = healthy ] && break
  sleep 2
done
[ "$status" = healthy ] || { docker compose logs --tail 40; die "HomeRoster does not start (see the logs above)."; }
ok "HomeRoster is running"

say ""; say "${B}4. Connection to your services${N}"
docker exec homeroster python -m homeroster check || warn "Some services are not reachable yet: check the URLs, keys and firewall."

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
DOMAIN=$(grep -E '^  domain:' "$CONFIG" | awk '{print $2}')
say ""; say "${B}${G}Ready.${N}"
say "  Point a proxy host of your reverse proxy (e.g. roster.$DOMAIN) to http://${IP:-<this host>}:8000,"
say "  restricted to your own VPN devices, then open it to create the admin account."
if ! docker exec homeroster python -c "import sqlite3,sys; c=sqlite3.connect('/data/homeroster.db'); r=c.execute('select totp_confirmed from admin').fetchone(); sys.exit(0 if r and r[0] else 1)" 2>/dev/null; then
  say "  ${B}Setup token (asked when creating the admin account): ${Y}$(env_get HOMEROSTER_SETUP_TOKEN)${N}"
fi
say "  Logs: docker compose logs -f   ·   Update: git pull && bash scripts/install.sh --update"
