#!/usr/bin/env bash
# QuantForge external VPS deployer — mirrors the dsh/comfy remote-gateway
# pattern: local FastAPI backend -> SSH reverse tunnel -> VPS Caddy
# (basic-auth + Let's Encrypt TLS) at https://74-211-104-188.nip.io:8445.
#
# Idempotent. Run from the QuantForge repo root:
#   ./deploy/vps/deploy.sh                 # deploy (auto-generates a login password on first run)
#   PASSWORD=MySecret ./deploy/vps/deploy.sh   # set a specific login password
#   ./deploy/vps/deploy.sh status          # show current status/URLs
#
# Config (first 5 variables) — edit to taste before running.
set -euo pipefail

# ─── Config ─────────────────────────────────────────────────────────────────
VPS_ALIAS="${VPS_ALIAS:-kiwi-vps}"              # local ssh alias for the VPS (see ~/.ssh/config)
HOST="${QUANTFORGE_HOST:-74-211-104-188.nip.io}"  # nip.io hostname resolving to the VPS public IP
TUN_PORT="${QUANTFORGE_TUN_PORT:-9445}"          # VPS loopback port for the reverse tunnel
PUB_PORT="${QUANTFORGE_PUB_PORT:-8445}"          # public Caddy port
LOCAL_BACKEND="${QUANTFORGE_LOCAL_BACKEND:-127.0.0.1:8000}"  # local backend host:port
LOGIN_USER="${QUANTFORGE_LOGIN_USER:-dsh}"       # Caddy basic-auth username
CADDYFILE="${QUANTFORGE_CADDYFILE:-/etc/caddy/Caddyfile}"
SSH_KEY="${QUANTFORGE_SSH_KEY:-$HOME/.ssh/id_ed25519_kiwi_dsh_tunnel}"
# ─────────────────────────────────────────────────────────────────────────────

PASS_STORE="$HOME/.config/quantforge/vps-login-password"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UNIT="$HOME/.config/systemd/user/quantforge-tunnel.service"

log() { echo -e "\033[1;36m[deploy]\033[0m $*"; }
die() { echo -e "\033[1;31m[deploy]\033[0m $*" >&2; exit 1; }

# --- password management -----------------------------------------------------
ensure_password() {
  if [ -n "${PASSWORD:-}" ]; then
    mkdir -p "$(dirname "$PASS_STORE")"
    chmod 700 "$(dirname "$PASS_STORE")"
    printf '%s' "$PASSWORD" > "$PASS_STORE"
    chmod 600 "$PASS_STORE"
    log "Using provided login password (saved to $PASS_STORE)"
    return
  fi
  if [ -s "$PASS_STORE" ]; then
    log "Reusing stored login password from $PASS_STORE"
    return
  fi
  local pw
  pw="$(openssl rand -base64 15 | tr -d '/+=' | cut -c1-16)"
  mkdir -p "$(dirname "$PASS_STORE")"
  chmod 700 "$(dirname "$PASS_STORE")"
  printf '%s' "$pw" > "$PASS_STORE"
  chmod 600 "$PASS_STORE"
  log "Generated new login password -> $PASS_STORE"
  echo -e "\033[1;33m  Public URL: https://$HOST:$PUB_PORT\033[0m"
  echo -e "\033[1;33m  Login:      user '$LOGIN_USER'  password '$pw'\033[0m"
  echo "  (you will not see it again; change it with PASSWORD=... ./deploy.sh)"
}

status_cmd() {
  echo "Public URL : https://$HOST:$PUB_PORT"
  echo "Login      : user '$LOGIN_USER' (password stored at $PASS_STORE)"
  echo "Backend    : $LOCAL_BACKEND"
  ssh -o ConnectTimeout=8 "$VPS_ALIAS" -- \
    "ss -tln 2>/dev/null | grep -E ':$PUB_PORT |:$TUN_PORT ' || true"
  systemctl --user is-active quantforge-tunnel.service 2>/dev/null \
    && echo "Tunnel     : active (systemd user service)" \
    || echo "Tunnel     : INACTIVE"
  curl -sk -o /dev/null -w "probe      : https://$HOST:$PUB_PORT -> HTTP %{http_code}\n" \
    "https://$HOST:$PUB_PORT/" || echo "probe      : unreachable"
}

# 1) local systemd user tunnel unit ------------------------------------------
setup_local_tunnel() {
  log "Installing systemd user unit quantforge-tunnel.service -> $UNIT"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=Reverse SSH tunnel: QuantForge dashboard to $VPS_ALIAS:$PUB_PORT
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/ssh -N -T \\
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \\
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \\
  -R 127.0.0.1:$TUN_PORT:$LOCAL_BACKEND $VPS_ALIAS
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now quantforge-tunnel.service
  log "Tunnel service enabled + started. Waiting for port $TUN_PORT on $VPS_ALIAS..."
  for _ in $(seq 1 15); do
    if ssh -o ConnectTimeout=8 "$VPS_ALIAS" -- "ss -tln 2>/dev/null | grep -q ':$TUN_PORT '" 2>/dev/null; then
      log "VPS is now listening on 127.0.0.1:$TUN_PORT"
      return 0
    fi
    sleep 1
  done
  die "Tunnel port $TUN_PORT did not come up on $VPS_ALIAS — check:"
  die "  journalctl --user -u quantforge-tunnel.service -n 30"
  die "  (common cause: the authorized_keys permitlisten list on the VPS must include $TUN_PORT)"
}

# 2) VPS: permitlisten + Caddy block -------------------------------------------
setup_remote() {
  local pubkey
  pubkey="$(awk '{print $1, $2}' "$SSH_KEY.pub" 2>/dev/null | awk '{print $2}')"
  log "Ensuring VPS authorized_keys allows reverse listen on $TUN_PORT for this key"
  ssh -o ConnectTimeout=10 "$VPS_ALIAS" -- bash -s <<REMOTE
set -e
KEYS=/root/.ssh/authorized_keys
if grep -q "$pubkey" \$KEYS; then
  if ! grep -q "permitlisten=\"127.0.0.1:$TUN_PORT\"" \$KEYS; then
    cp \$KEYS \$KEYS.bak.\$(date +%s)
    python3 - "\$KEYS" "$pubkey" "$TUN_PORT" <<'PY'
import sys
path, key, port = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().splitlines()
out = []
for ln in lines:
    if key in ln and ('permitlisten' in ln) and 'permitlisten="127.0.0.1:' + port + '"' not in ln:
        # append the new permitlisten after the last one (or after port-forwarding)
        ln = ln.replace('permitlisten="127.0.0.1:9444"',
                        'permitlisten="127.0.0.1:9444",permitlisten="127.0.0.1:' + port + '"')
    out.append(ln)
open(path, 'w').write("\n".join(out) + "\n")
PY
    echo "  added permitlisten for $TUN_PORT"
  else
    echo "  permitlisten for $TUN_PORT already present"
  fi
else
  echo "  WARN: key $pubkey not found in \$KEYS; ensure your key is authorized"
fi
REMOTE

  local pass
  pass="$(cat "$PASS_STORE")"
  log "Hashing login password on $VPS_ALIAS (caddy hash-password)"
  local hash
  hash="$(ssh -o ConnectTimeout=10 "$VPS_ALIAS" -- "caddy hash-password --plaintext '$pass'" | tail -1)"

  log "Adding QuantForge site block to $CADDYFILE (if absent)"
  ssh -o ConnectTimeout=10 "$VPS_ALIAS" -- bash -s <<REMOTE
set -e
CADDYFILE="$CADDYFILE"
HOST="$HOST"
PUB_PORT="$PUB_PORT"
TUN_PORT="$TUN_PORT"
LOGIN_USER="$LOGIN_USER"
HASH="$hash"
if grep -q ":$PUB_PORT {" "\$CADDYFILE"; then
  echo "  site block for :$PUB_PORT already present"
else
  cp "\$CADDYFILE" "\$CADDYFILE.bak.\$(date +%s)"
  cat >> "\$CADDYFILE" <<CADDY

# quantforge remote gateway (deployed by deploy/vps/deploy.sh)
https://\$HOST:\$PUB_PORT {
	@needs_auth not path /api/*
	basic_auth @needs_auth {
		\$LOGIN_USER \$HASH
	}
	reverse_proxy 127.0.0.1:\$TUN_PORT {
		transport http {
			tls_insecure_skip_verify
		}
	}
}
CADDY
  echo "  appended site block for :$PUB_PORT"
fi
echo "  validating Caddyfile..."
caddy validate --config "\$CADDYFILE" >/dev/null 2>&1
caddy reload --config "\$CADDYFILE" >/dev/null 2>&1
echo "  caddy reloaded"
REMOTE
}

# ─── main ─────────────────────────────────────────────────────────────────────
case "${1:-deploy}" in
  status) status_cmd; exit 0 ;;
  deploy) ;;
  *) die "usage: $0 [deploy|status]";;
esac

command -v ssh >/dev/null || die "ssh required"
command -v caddy >/dev/null || true

ensure_password
setup_local_tunnel
setup_remote

log "Deployed. Public URL:"
echo -e "\033[1;32m  https://$HOST:$PUB_PORT  (user '$LOGIN_USER', password in $PASS_STORE)\033[0m"
echo "  Try: curl -sk -u '$LOGIN_USER:********' https://$HOST:$PUB_PORT/api/health"
