#!/usr/bin/env bash
#
# One-time provisioning for a fresh Ubuntu 22.04/24.04 AWS Lightsail instance.
# Installs Python deps, Caddy (auto-HTTPS), and a systemd timer that pulls the
# latest code and runs the pipeline every few hours.
#
# Run it FROM INSIDE the cloned repo, as root:
#     cd /opt/openclaw_status_app
#     sudo deploy/provision.sh clawstat.us
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

# --- ownership + serve permissions -----------------------------------------
# Caddy runs as its own user, so it can only read web/ via the *world* bits. A
# non-default umask on the operator's clone could strip those (→ 403s on first
# load), so force web/ world-readable+traversable and the app root traversable,
# independent of how the repo was cloned. .env is locked last so it stays 600.
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod -R a+rX "$APP_DIR/web"   # files world-readable, dirs world-traversable
chmod o+x "$APP_DIR"           # let Caddy traverse the app root into web/
chmod 600 "$APP_DIR/.env"

# --- git: allow any user (operator/agent/timer) to run git in the repo ------
# .git is owned by APP_USER, so a pull run as root or the operator would trip
# git's "dubious ownership" guard. --system whitelists the repo for every user.
git config --system --add safe.directory "$APP_DIR"

# --- small-instance hardening ----------------------------------------------
# The small Lightsail plans have no swap. fwupd (firmware updates, nothing to
# update on a VM) needs ~170 MB on its hourly metadata refresh and was
# OOM-killed 150+ times; one run thrashed the box for 20 min, stalling Caddy
# and knocking systemd-networkd off the NIC. Mask it, timer included, so a
# package upgrade can't bring it back.
systemctl disable --now fwupd-refresh.timer 2>/dev/null || true
systemctl mask fwupd.service fwupd-refresh.service fwupd-refresh.timer
# A hung `apt-get update` once kept apt-daily.service "activating" for 46 days,
# which also blocks apt-daily-upgrade (unattended security updates). That job
# only downloads, so killing a stuck run is safe.
mkdir -p /etc/systemd/system/apt-daily.service.d
cat > /etc/systemd/system/apt-daily.service.d/50-timeout.conf <<'UNIT'
[Service]
TimeoutStartSec=1h
UNIT

# --- systemd timer ---------------------------------------------------------
install -m644 "$APP_DIR/deploy/openclaw-status.service"         /etc/systemd/system/
install -m644 "$APP_DIR/deploy/openclaw-status-failure.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/openclaw-status.timer"           /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now openclaw-status.timer

# --- Caddy site ------------------------------------------------------------
# The box may serve other sites too. Each site keeps its own file in /etc/caddy/sites/, and
# /etc/caddy/Caddyfile only imports that folder, so this never touches another site's settings.
SITES=/etc/caddy/sites
MAIN=/etc/caddy/Caddyfile
IMPORT="import $SITES/*.caddy"
# The main Caddyfile becomes the one-line importer. Replace only Caddy's sample or this site's own
# Caddyfile from before the sites/ folder: anything else may hold another site, so stop before
# changing anything.
if [ -f "$MAIN" ] && ! grep -qxF "$IMPORT" "$MAIN" && ! grep -qxF "$DOMAIN {" "$MAIN" \
    && ! grep -qF 'The Caddyfile is an easy way' "$MAIN"; then
  echo "!! $MAIN holds settings this script didn't write. Move them to $SITES/<site>.caddy, then rerun." >&2
  exit 1
fi
install -d -m 755 "$SITES"
# Check the site on its own before it replaces the one in use (the .new name is outside the
# import pattern), so a typo can't leave Caddy unable to start on its next restart.
sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/Caddyfile" > "$SITES/$DOMAIN.new"
caddy validate --config "$SITES/$DOMAIN.new" --adapter caddyfile || { rm -f "$SITES/$DOMAIN.new"; exit 1; }
install -m 644 "$SITES/$DOMAIN.new" "$SITES/$DOMAIN.caddy"
rm -f "$SITES/$DOMAIN.new"
grep -qxF "$IMPORT" "$MAIN" 2>/dev/null \
  || printf '# Each site on this box has its own file in %s/.\n%s\n' "$SITES" "$IMPORT" > "$MAIN"
# Check all the sites together too (two sites can't share a name or a snippet name).
caddy validate --config "$MAIN" --adapter caddyfile
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
