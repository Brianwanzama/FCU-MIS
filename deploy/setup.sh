#!/usr/bin/env bash
# First-time setup of FCU-MIS on a fresh Ubuntu 24.04 Droplet. Run as root:
#
#   sudo ./setup.sh mis.example.org you@example.org
#
# The domain must already point at this Droplet (an A record), or the HTTPS
# certificate step cannot work. Set SKIP_CERTBOT=1 to do that step later.
set -euo pipefail

DOMAIN=${1:?Usage: setup.sh <domain> <email-for-certificate>}
EMAIL=${2:?Usage: setup.sh <domain> <email-for-certificate>}
REPO=${REPO_URL:-https://github.com/Brianwanzama/FCU-MIS.git}
BRANCH=${BRANCH:-main}
APP=/opt/fcu-mis
DATA=/var/lib/fcu-mis
ENV_FILE=/etc/fcu-mis/env

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev build-essential libpq-dev \
  postgresql nginx certbot python3-certbot-nginx git ufw fail2ban unattended-upgrades curl

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || { echo "Python 3.12 or newer is required (Ubuntu 24.04 has it)."; exit 1; }

echo "==> Service user and folders"
id fcu >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/fcu --shell /usr/sbin/nologin fcu
mkdir -p "$DATA/media" "$DATA/private" /etc/fcu-mis /var/backups/fcu-mis
chown -R fcu:fcu "$DATA"
chmod 750 "$DATA"
chmod 700 /var/backups/fcu-mis

echo "==> Code"
if [ ! -d "$APP/.git" ]; then
  git clone --branch "$BRANCH" "$REPO" "$APP"
fi
chown -R fcu:fcu "$APP"

echo "==> Settings file ($ENV_FILE)"
if [ ! -f "$ENV_FILE" ]; then
  SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(60))')
  DBPASS=$(python3 -c 'import secrets; print(secrets.token_hex(24))')

  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE fcu LOGIN PASSWORD '$DBPASS'" 
  sudo -u postgres createdb -O fcu fcu_mis

  cat > "$ENV_FILE" <<ENVEOF
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_SECRET_KEY=$SECRET
DATABASE_URL=postgres://fcu:$DBPASS@localhost:5432/fcu_mis
DJANGO_ALLOWED_HOSTS=$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN
FCU_MEDIA_ROOT=$DATA/media
FCU_PRIVATE_MEDIA_ROOT=$DATA/private
FCU_LOAN_LIMIT_PERCENT=100
FCU_LOAN_AUTO_RECOVERY_ENABLED=False
FCU_LOAN_RECOVERY_POLICY_REF=FCU Manual v3.0, section 4.6

# Email: fill these in so password-reset and activation emails reach members.
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=no-reply@$DOMAIN
ENVEOF
  chown root:fcu "$ENV_FILE"
  chmod 640 "$ENV_FILE"
else
  echo "    $ENV_FILE already exists: left unchanged."
fi

echo "==> Python environment, database, static files"
sudo -u fcu python3 -m venv "$APP/venv"
sudo -u fcu "$APP/venv/bin/pip" install -q --upgrade pip
sudo -u fcu "$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

run_manage() { sudo -u fcu bash -c "set -a; . $ENV_FILE; set +a; cd $APP; $APP/venv/bin/python manage.py $*"; }
run_manage migrate --noinput
run_manage collectstatic --noinput

echo "==> Services"
cp "$APP"/deploy/fcu-mis.service "$APP"/deploy/fcu-recover-loans.service "$APP"/deploy/fcu-recover-loans.timer \
   "$APP"/deploy/fcu-backup.service "$APP"/deploy/fcu-backup.timer /etc/systemd/system/
chmod +x "$APP"/deploy/backup.sh "$APP"/deploy/update.sh
systemctl daemon-reload
systemctl enable --now fcu-mis fcu-recover-loans.timer fcu-backup.timer

echo "==> Nginx"
cp "$APP/deploy/nginx-ratelimit.conf" /etc/nginx/conf.d/fcu-ratelimit.conf
sed "s/__DOMAIN__/$DOMAIN/g" "$APP/deploy/nginx-fcu-mis.conf" > /etc/nginx/sites-available/fcu-mis
ln -sf /etc/nginx/sites-available/fcu-mis /etc/nginx/sites-enabled/fcu-mis
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

if [ "${SKIP_CERTBOT:-0}" != "1" ]; then
  echo "==> HTTPS certificate"
  certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email -n --redirect
fi

echo "==> Checks"
sleep 3
curl -fsS --unix-socket /run/fcu-mis/gunicorn.sock -H "Host: localhost" -H "X-Forwarded-Proto: https" http://localhost/healthz/ && echo
run_manage preflight || true

cat <<DONE

FCU-MIS is installed.

Next:
  1. Create the first administrator:  sudo -u fcu bash -c "set -a; . $ENV_FILE; set +a; cd $APP; $APP/venv/bin/python manage.py createsuperuser"
  2. Open https://$DOMAIN/accounts/activate/ and activate FCU001 or FCU004.
  3. Fill in the email settings in $ENV_FILE, then:  systemctl restart fcu-mis
  4. Copy /var/backups/fcu-mis to somewhere OFF this server regularly.
  See docs/DEPLOY_DROPLET.md.
DONE
