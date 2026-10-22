# Hosting FCU-MIS on a DigitalOcean Droplet

One small server runs everything: Nginx (HTTPS) in front of the app (gunicorn), PostgreSQL, the
daily loan check and nightly backups. The setup script does the work; this page says what to
prepare and what to do afterwards.

> The files in `deploy/` were checked for syntax and the app was tested behind a gunicorn
> socket with the same settings, but the script has **not been run on a real Droplet** yet.
> Do the first install on a new, empty Droplet and read its output.

## 1. What you need

| | |
| --- | --- |
| Droplet | Ubuntu 24.04 LTS, **2 GB RAM / 1 vCPU** (about $12 a month) is plenty for FCU. Turn on **Backups** (weekly snapshots, about 20% extra). |
| Domain | A name you control, such as `mis.fcu.org`. HTTPS needs one. |
| DNS | An **A record** for that name pointing at the Droplet's IP address, made *before* you run the script. |
| Access | Add your SSH key when creating the Droplet. Do not use password login. |
| Email | SMTP details from your mail provider (for password resets and account activation). |

If the repository is private, the script cannot clone it without a token. Either make a
read-only deploy key on GitHub, or clone it yourself to `/opt/fcu-mis` first.

## 2. Install

```bash
ssh root@YOUR_DROPLET_IP
apt-get update && apt-get install -y git
git clone https://github.com/Brianwanzama/FCU-MIS.git /opt/fcu-mis
cd /opt/fcu-mis/deploy
./setup.sh mis.example.org you@example.org      # your domain and your email
```

It installs the packages, creates a `fcu` service user and the database, writes
`/etc/fcu-mis/env` with a fresh secret key and database password, runs the migrations, sets up
Nginx with a login rate limit, the firewall (SSH, HTTP, HTTPS only), a Let's Encrypt certificate that
renews itself, a daily loan check and a nightly backup, then runs `preflight`.

If DNS is not ready yet: `SKIP_CERTBOT=1 ./setup.sh ...`, then later
`certbot --nginx -d mis.example.org`.

## 3. First use

1. Create the technical administrator:
   `sudo -u fcu bash -c "set -a; . /etc/fcu-mis/env; set +a; cd /opt/fcu-mis; venv/bin/python manage.py createsuperuser"`
2. Visit `https://mis.example.org/accounts/activate/` and activate FCU001 or FCU004.
3. Edit `/etc/fcu-mis/env` and fill in the `EMAIL_*` lines, then `systemctl restart fcu-mis`.
4. Add the six missing member emails in Django Admin, then create and activate the first cycle.
5. Run the checklist: `cd /opt/fcu-mis && sudo -u fcu bash -c "set -a; . /etc/fcu-mis/env; set +a; venv/bin/python manage.py preflight"`.

## 4. Updating

```bash
sudo /opt/fcu-mis/deploy/update.sh          # takes a backup, pulls main, migrates, restarts, checks health
```

## 5. Backups (important)

- `fcu-backup.timer` writes a database dump and a copy of the uploaded forms to
  `/var/backups/fcu-mis` every night at 02:30 and keeps 14 days.
- Those are on the **same server**, so they do not survive losing the Droplet. Also copy them off it,
  for example with a scheduled `rsync`/`scp` to another machine, or to DigitalOcean Spaces. DigitalOcean's
  own weekly Droplet backups are a second layer; keep both.
- **Test a restore** before you rely on it, on a spare Droplet:
  `gunzip -c db-DATE.sql.gz | psql "$DATABASE_URL"` and `tar -xzf files-DATE.tar.gz -C /var/lib/fcu-mis`.

## 6. Loan recovery

`fcu-recover-loans.timer` runs `recover_overdue_loans` every day at 01:30. It only reports what it would
do until FCU approves the policy. Then set `FCU_LOAN_AUTO_RECOVERY_ENABLED=True` and
`FCU_LOAN_RECOVERY_POLICY_REF=<the resolution>` in `/etc/fcu-mis/env` and `systemctl restart fcu-mis`.
See what ran: `journalctl -u fcu-recover-loans`.

## 7. Everyday commands

| | |
| --- | --- |
| Is the app up? | `systemctl status fcu-mis` and `curl https://mis.example.org/healthz/` |
| Logs | `journalctl -u fcu-mis -f` (app), `/var/log/nginx/error.log` (web server) |
| Restart | `systemctl restart fcu-mis` |
| Timers | `systemctl list-timers 'fcu-*'` |

Set a free uptime monitor (for example UptimeRobot) on `https://mis.example.org/healthz/` so you hear
when it is down.

## 8. Security notes

- Only SSH (22), HTTP (80) and HTTPS (443) are open. `fail2ban` is installed by the script. Consider
  moving SSH to keys only (`PasswordAuthentication no`) and keeping `unattended-upgrades` on.
- The settings file `/etc/fcu-mis/env` holds the secret key and the database password: it is readable
  only by root and the `fcu` user. Never put it in git.
- PostgreSQL listens only on the Droplet itself. Uploaded forms are stored outside the web root and are
  served only through the app, after a permission check.
- National ID numbers and signed forms are personal data: limit who has SSH access, and keep the backups
  protected.

## 9. Droplet or Railway?

| | Droplet | Railway |
| --- | --- | --- |
| Cost | Fixed, a few dollars | Pay by usage, usually a little more |
| Effort | You maintain the server (updates, backups, security) | Mostly managed |
| Control | Full | Limited |
| Data location | A DigitalOcean region you choose | Railway's regions |

Either works. A Droplet is a good fit if you want a fixed price and are comfortable (or have someone who is)
looking after a server.
