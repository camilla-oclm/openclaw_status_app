#!/usr/bin/env bash
#
# One-time provisioning for a fresh Ubuntu 22.04/24.04 AWS Lightsail instance.
# Installs Python deps, Caddy (auto-HTTPS), and a systemd timer that pulls the
# latest code and runs the pipeline every few hours.
#
# Run it FROM INSIDE the cloned repo, as root:
#     cd /opt/openclaw_status_app
#     sudo deploy/provision.sh openclawstatus.io
#
set -euo pipefail

DOMAIN="${1:?usage: sudo deploy/provision.sh <domain>}"
APP_USER=openclaw
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> App dir : $APP_DIR"
echo "==> Domain  : $DOMAIN"

# --- system user (nologin; owns the app + runs the timer) ------------------
id -u "$APP_USER" &>/dev/null \
  || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

# --- base packages ---------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl \
                   debian-keyring debian-archive-keyring apt-transport-https

# --- Caddy (official apt repo) ---------------------------------------------
if ! command -v caddy &>/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

# --- python venv + deps ----------------------------------------------------
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# --- .env (operator fills in the two keys) ---------------------------------
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "!! Created $APP_DIR/.env — set OPENROUTER_API_KEY + GITHUB_TOKEN before the first run."
fi

# --- ownership (app world-readable so Caddy can serve web/; .env locked) ----
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

# --- systemd timer ---------------------------------------------------------
install -m644 "$APP_DIR/deploy/openclaw-status.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/openclaw-status.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now openclaw-status.timer

# --- Caddy site ------------------------------------------------------------
sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

cat <<EOF

==> Provisioned. Remaining steps:
    1) Edit $APP_DIR/.env        (OPENROUTER_API_KEY + GITHUB_TOKEN)
    2) Point $DOMAIN A-record at this box's static IP (if not done yet)
    3) Seed the first page:
         sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/run.py full
    4) Open https://$DOMAIN

    Logs:   journalctl -u openclaw-status.service -f
    Timer:  systemctl list-timers openclaw-status.timer
EOF
