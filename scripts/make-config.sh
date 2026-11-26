#!/usr/bin/env bash
# =====================================================================================================
#  Write a homeroster.yaml from the annotated example, keeping only the chosen VPN, proxy and apps.
#  Used by scripts/install.sh (Docker) and scripts/proxmox/install.sh (LXC / bare Debian).
#
#    VPN=wg-easy PROXY=npm APPS="jellyfin seerr immich" LANGUAGE=fr OWNER=Alex DOMAIN=example.com \
#    PROXY_IP=192.168.1.10 bash scripts/make-config.sh config/homeroster.example.yaml config/homeroster.yaml
#
#  The example marks each choice with "# >>> vpn: <name>", "# >>> proxy: <name>" or "# >>> app: <name>"
#  ... "# <<<": the chosen blocks are kept (uncommented), the others removed.
# =====================================================================================================
set -euo pipefail
EXAMPLE=$1; OUT=$2
: "${VPN:?}" "${PROXY:?}" "${APPS:=jellyfin seerr immich openwebui}" "${LANGUAGE:=en}" "${OWNER:=Alex}"
: "${DOMAIN:=example.com}" "${PROXY_IP:=192.168.1.10}"

# Seerr signs in with Jellyfin or Emby accounts.
PARENT=""; for a in jellyfin emby; do [[ " $APPS " == *" $a "* ]] && { PARENT=$a; break; }; done
[ -n "$PARENT" ] || APPS=${APPS//seerr/}

awk -v vpn="$VPN" -v proxy="$PROXY" -v apps=" $APPS " '
  /^# >>> / { kind = $3; sub(":", "", kind)
              keep = (kind == "vpn" && $4 == vpn) || (kind == "proxy" && $4 == proxy) || (kind == "app" && index(apps, " " $4 " "))
              block = 1; next }
  /^# <<</  { block = 0; next }
  /^#  Pick ONE/ { next }
  block && !keep { next }
  block { sub(/^# /, ""); print; next }
  { print }' "$EXAMPLE" |
sed -e "s/^  owner: .*/  owner: $OWNER/" -e "s/^  domain: .*/  domain: $DOMAIN/" \
    -e "s/^  language: en .*/  language: $LANGUAGE/" -e "s/^    - 192.168.1.10$/    - $PROXY_IP/" \
    -e "s/^  from_name: .*/  from_name: $OWNER's homelab/" -e "s/^    uses: jellyfin .*/    uses: ${PARENT:-jellyfin}/" > "$OUT"
