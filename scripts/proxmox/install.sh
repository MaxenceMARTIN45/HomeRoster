#!/usr/bin/env bash
# =====================================================================================================
#  Native HomeRoster install on Debian 12 / Ubuntu 22.04+ (what the Proxmox LXC script runs inside the
#  container; it also works on any such machine or VM, as root):
#
#    VPN=wg-easy PROXY=npm APPS="jellyfin seerr immich" bash install.sh
#
#  /opt/homeroster       the code (a git checkout) and its virtualenv
#  /etc/homeroster       homeroster.yaml and .env (secrets, mode 600): never touched by updates
#  /var/lib/homeroster   the database and services.yaml: never touched by updates
#  systemd unit "homeroster" on port 8000, user "homeroster"; the command `update` upgrades in place.
# =====================================================================================================
set -euo pipefail
REPO=${REPO:-MaxenceMARTIN45/HomeRoster}
BRANCH=${BRANCH:-develop}
APP=/opt/homeroster; ETC=/etc/homeroster; DATA=/var/lib/homeroster
export DEBIAN_FRONTEND=noninteractive

echo "  · installing packages"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv >/dev/null

id homeroster >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin homeroster
mkdir -p "$ETC" "$DATA"

echo "  · downloading HomeRoster ($REPO, $BRANCH)"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q --depth 1 origin "$BRANCH" && git -C "$APP" reset -q --hard FETCH_HEAD
else
  git clone -q --depth 1 --branch "$BRANCH" "https://github.com/$REPO.git" "$APP"
fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$ETC/homeroster.yaml" ]; then
  : "${VPN:=wg-easy}" "${PROXY:=npm}"
  VPN=$VPN PROXY=$PROXY APPS="${APPS:-jellyfin seerr immich openwebui}" LANGUAGE="${LANGUAGE:-en}" OWNER="${OWNER:-Alex}" \
    DOMAIN="${DOMAIN:-example.com}" PROXY_IP="${PROXY_IP:-192.168.1.10}" \
    bash "$APP/scripts/make-config.sh" "$APP/config/homeroster.example.yaml" "$ETC/homeroster.yaml"
  # File-based providers live in the container: point them at local paths.
  sed -i -e "s#/wireguard/wg0.conf#/etc/wireguard/wg0.conf#" -e "s#^  dir: /proxy#  dir: $DATA/proxy#" \
         -e "s#/traefik/homeroster.yml#$DATA/traefik/homeroster.yml#" "$ETC/homeroster.yaml"
fi
if [ ! -f "$ETC/.env" ]; then
  umask 077
  {
    echo "# HomeRoster secrets. Fill them here, or type them in Settings > Services."
    echo "HOMEROSTER_SETUP_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
    echo "TZ=$(cat /etc/timezone 2>/dev/null || echo UTC)"
    grep -v '^\s*#' "$ETC/homeroster.yaml" | grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*\}' | tr -d '${}' | sort -u | sed 's/$/=/'
  } > "$ETC/.env"
fi
chown -R homeroster:homeroster "$DATA"
chown root:homeroster "$ETC" "$ETC/homeroster.yaml" "$ETC/.env"
chmod 750 "$ETC"; chmod 640 "$ETC/homeroster.yaml" "$ETC/.env"; chmod 700 "$DATA"

cat > /etc/systemd/system/homeroster.service <<EOF
[Unit]
Description=HomeRoster
After=network-online.target
Wants=network-online.target

[Service]
User=homeroster
Group=homeroster
WorkingDirectory=$APP
EnvironmentFile=$ETC/.env
Environment=PYTHONPATH=$APP/src HOMEROSTER_CONFIG=$ETC/homeroster.yaml HOMEROSTER_DATA=$DATA
ExecStart=$APP/.venv/bin/python -m homeroster serve
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA

[Install]
WantedBy=multi-user.target
EOF
install -m 755 "$APP/scripts/proxmox/update.sh" /usr/local/bin/update
systemctl daemon-reload
systemctl enable -q --now homeroster
systemctl restart homeroster

for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/healthz >/dev/null || { journalctl -u homeroster -n 40 --no-pager; exit 1; }
echo "  ✓ HomeRoster $(git -C "$APP" rev-parse --short HEAD) is running on port 8000"
