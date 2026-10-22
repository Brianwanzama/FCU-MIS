#!/usr/bin/env bash
# Deploy the latest code: run as root on the Droplet.   sudo /opt/fcu-mis/deploy/update.sh
set -euo pipefail

APP=/opt/fcu-mis
BRANCH=${1:-main}

cd "$APP"
echo "Taking a backup first..."
"$APP/deploy/backup.sh"

sudo -u fcu git fetch origin "$BRANCH"
sudo -u fcu git checkout "$BRANCH"
sudo -u fcu git pull --ff-only origin "$BRANCH"
sudo -u fcu "$APP/venv/bin/pip" install -q -r requirements.txt

run_manage() { sudo -u fcu bash -c "set -a; . /etc/fcu-mis/env; set +a; cd $APP; $APP/venv/bin/python manage.py $*"; }

run_manage migrate --noinput
run_manage collectstatic --noinput
systemctl restart fcu-mis
sleep 3
curl -fsS --unix-socket /run/fcu-mis/gunicorn.sock -H "Host: localhost" -H "X-Forwarded-Proto: https" http://localhost/healthz/ && echo
run_manage preflight || true
echo "Updated to $(sudo -u fcu git rev-parse --short HEAD)."
