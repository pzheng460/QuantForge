# QuantForge — external VPS deployment

This directory deploys the QuantForge web dashboard so you can reach it from
outside (phone / laptop / any browser) over the internet, using the **same
architecture already used for DeepSeek Harness** on this box.

## Result (already live)

| Item | Value |
|---|---|
| Public URL | `https://74-211-104-188.nip.io:8445` |
| Login | user `dsh`, password stored at `~/.config/quantforge/vps-login-password` |
| Backend | local FastAPI `127.0.0.1:8000` (kept running, unchanged) |
| Reverse tunnel port | `9445` (VPS loopback ← SSH `-R 9445:8000`) |
| Caddy public port | `8445` (VPS, Let's Encrypt TLS) |

## Architecture

```
phone / outside ──HTTPS + password(login)──► Caddy on kiwi VPS :8445
                                              │ reverse_proxy 127.0.0.1:9445
                                              ▼
                                      SSH reverse tunnel
                                      (systemd user unit
                                       quantforge-tunnel.service)
                                              │
                                              ▼
                          local QuantForge FastAPI backend :8000
                          (prod mode, serves /api + built SPA dist)
```

- **Caddy** on the VPS (`74.211.104.188`) terminates TLS with a real Let's
  Encrypt certificate for `74-211-104-188.nip.io` and enforces a **password
  login** over the whole site. It already hosted `dsh` (`:8443`) and `comfyui`
  (`:8444`) the same way; QuantForge is the third block on `:8445`.
- **SSH reverse tunnel** (`systemd --user` service) forwards the VPS loopback
  port `9445` to the local backend `127.0.0.1:8000`. It mirrors
  `dsh-tunnel.service` (`9443`) and `comfy-tunnel.service` (`9444`).
- The **backend is left untouched** — still `uv run uvicorn ... --host 0.0.0.0
  --port 8000` over HTTPS. Caddy's reverse proxy uses
  `tls_insecure_skip_verify` because the backend cert is a self-signed localhost
  cert; Caddy is the real public TLS terminator.

### Auth model
- The **page and its static assets** sit behind the Caddy **password login** —
  you must enter the password to load the app at all.
- All `/api/*` (data **and** live-trading controls, and the `/api/ws/*` optimize
  progress WebSocket) is **exempt from the Caddy password**: browsers do not
  attach cached HTTP basic-auth credentials to `fetch()`/WebSocket requests, and
  doing so would 401 every API call and break the SPA (this is exactly why dsh
  exempts `/api/events.*` and comfyui exempts `/api/*`).

> ⚠️ **Security note:** because the SPA calls `/api/*` without credentials, the
> `/api/*` endpoints are reachable *without* the password (only the page is
> gated). Anyone who knows this URL could call `/api/live/*` directly. If you
> need per-API authentication, enable the backend's native
> `QUANTFORGE_API_KEY` and add a UI to enter it in the frontend (the client
> already sends `X-API-Key` and `?api_key=` from `localStorage['qf_api_key']`).
> If you prefer strong protection now, put the whole app behind a VPN/WireGuard
> instead of a public basic-auth page.

> ⚠️ **Note on `:8000` exposure:** because you chose to keep the backend on
> `0.0.0.0:8000`, that port is still reachable on the public address directly
> (bypassing the Caddy password). If you want the password login to be the only
> path, bind the backend to `127.0.0.1:8000` instead — the tunnel already points
> at loopback, so nothing else changes.

## One-command deploy

```sh
cd /home/pzheng46/QuantForge
./deploy/vps/deploy.sh                 # deploy (first run generates a password)
./deploy/vps/deploy.sh status          # show current status
PASSWORD=MySecret ./deploy/vps/deploy.sh   # set a specific login password
```

`deploy.sh` is idempotent and does all of:
1. Installs/enables the local **`quantforge-tunnel.service`** systemd user unit
   (`-R 127.0.0.1:9445:127.0.0.1:8000 kiwi-vps`), so it auto-restarts and
   survives reboots.
2. On the VPS, adds `permitlisten="127.0.0.1:9445"` to the tunnel key's
   `authorized_keys` entry (required or sshd refuses to forward the new port).
3. Generates/hashes the login password (`caddy hash-password`), appends the
   QuantForge site block to `/etc/caddy/Caddyfile`, validates, and reloads
   Caddy.

## Manual reference files

- `Caddyfile.quantforge.snippet` — the exact site block added to the VPS
  `/etc/caddy/Caddyfile`.
- `quantforge-tunnel.service` — the systemd user unit for the reverse tunnel.

## Verify

```sh
curl -sk -o /dev/null -w "%{http_code}\n" https://74-211-104-188.nip.io:8445/
# 401 (no creds → login prompt)
curl -sk -u dsh:PASSWORD https://74-211-104-188.nip.io:8445/api/health
# {"status":"ok"}
```

## Change the login password

```sh
cd /home/pzheng46/QuantForge && PASSWORD='NewPass123' ./deploy/vps/deploy.sh
```

The script re-hashes and reloads Caddy but keeps the tunnel running.

## Troubleshooting

- **Port 9445 doesn't come up on the VPS** — the `authorized_keys` entry for the
  tunnel key must include `permitlisten="127.0.0.1:9445"`. `deploy.sh` handles
  this; a fresh `authorized_keys` (e.g. after re-keying) will need it again.
- **Tunnel stopped** — `systemctl --user status quantforge-tunnel.service`;
  it auto-restarts. `journalctl --user -u quantforge-tunnel.service -n 30`.
- **Backend not serving SPA** — rebuild it first:
  `cd apps/dashboard/frontend && npm run build`, then restart the backend.
