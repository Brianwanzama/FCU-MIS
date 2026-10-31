# Deploying FCU-MIS on Railway

> Prefer a DigitalOcean Droplet? See **docs/DEPLOY_DROPLET.md**.

Everything here was checked by running the production settings locally (HTTPS redirect,
host checking, secure cookies and headers, hashed static files, health check). What
cannot be checked from outside Railway is listed under "Only you can verify".

## 1. Services

1. **PostgreSQL** plugin. Railway sets `DATABASE_URL` for you.
2. **Web service** from this GitHub repository. `railway.json` already tells Railway to:
   - build: `collectstatic` with the production settings;
   - start: `migrate`, then `gunicorn`;
   - health check: `GET /healthz/` (public, answers `{"status": "ok"}`).
3. **A volume** mounted on the web service (for example at `/data`), because the service's
   own disk is wiped on every deploy. Uploaded loan forms and signed declarations live there.
4. **A cron service** (same repository) that runs once a day, with nobody logged in:
   `DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py recover_overdue_loans`
   It only reports what it would do until recovery is switched on (below).

## 2. Variables (Railway > web service > Variables)

| Variable | Value |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `DJANGO_SECRET_KEY` | a new long random value: `python -c "import secrets; print(secrets.token_urlsafe(60))"` |
| `DATABASE_URL` | provided by the PostgreSQL plugin |
| `FCU_PRIVATE_MEDIA_ROOT` | `/data/private` (on the volume) |
| `FCU_MEDIA_ROOT` | `/data/media` (on the volume) |
| `DJANGO_ALLOWED_HOSTS` | only if you use a custom domain, e.g. `mis.example.org` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | the same domain with `https://`, e.g. `https://mis.example.org` |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | your mail provider. Without these, password-reset emails never reach members. |
| `FCU_LOAN_LIMIT_PERCENT` | `100` (FCU decision) |
| `FCU_LOAN_AUTO_RECOVERY_ENABLED` | `True` (default): overdue loans are recovered from savings automatically; `False` switches it off |
| `FCU_LOAN_RECOVERY_POLICY_REF` | the authority, e.g. the General Meeting resolution |

The cron service needs the same variables as the web service (use Railway's shared variables).

The old variables `FCU_DEFAULT_MAX_LOAN_AMOUNT` and `FCU_DEFAULT_MAX_LOAN_PERCENT_OF_CONTRIBUTIONS`
are no longer read; delete them if they exist.

## 3. First deployment

1. Deploy. `migrate` runs automatically and creates the 19 members and the two forms.
2. Open a shell on the web service and run `python manage.py createsuperuser` (technical
   break-glass account only).
3. Visit `/accounts/activate/` and activate FCU001 or FCU004 to get a real Administrator
   tied to a member.
4. In Django Admin, add the missing member emails (6 members have none).
5. Create and activate the first cycle (Cycles menu).
6. Run **`python manage.py preflight`**. Fix every `FAIL`; read every `warn`.

Run `preflight` again after every deployment. `--strict` makes warnings fail too.

## 4. What `preflight` and the CI check

- `DEBUG` off, strong secret key, no wildcard hosts, secure cookies and HTTPS redirect.
- Django's own `check --deploy`.
- Database reachable, no unapplied migrations.
- Uploads and media folders writable and (on Railway) set to the volume.
- Static files collected.
- An Administrator exists, the forms are in the Document Library, a cycle is active.

GitHub Actions (`.github/workflows/ci.yml`) runs on every push: Django check, "no model
change without a migration", production deployment checks, `collectstatic` with the
production storage, and the whole test suite against PostgreSQL 16.

## 5. Before go-live (security)

- **`.env.example` once contained real-looking passwords (`Einstein123`) and they are in the
  git history.** Treat them as exposed: change the database and email passwords anywhere
  they were used, and never reuse them. Removing them from the file does not remove them
  from history.
- Make sure no `.env` file is committed (it is in `.gitignore`).
- Turn on Railway's automatic backups for PostgreSQL, and also take a manual dump before
  any risky change: `pg_dump "$DATABASE_URL" > fcu-backup.sql`. Back up the volume too
  (the uploaded forms are not in the database).
- Members' national ID numbers and signed forms are personal data: limit who has Railway
  and GitHub access.

## 6. Automatic loan recovery

1. It is **on by default**: the first time anyone signs in each day the system checks for loans past their
   agreed repayment period and recovers what is owed from the member's deposits (emergency excluded).
   No scheduler is needed; a Railway cron running `python manage.py recover_overdue_loans` is optional.
2. Set `FCU_LOAN_RECOVERY_POLICY_REF` to the General Meeting resolution so it is written to the audit log.
3. The Loan Register shows what each overdue loan would recover. Every recovery is in the audit log.
4. To switch it off set `FCU_LOAN_AUTO_RECOVERY_ENABLED=False`.

## 7. Only you can verify

I cannot see Railway or the GitHub Actions run from here. After you deploy, check:

- `https://<your-domain>/healthz/` returns `{"status": "ok"}`.
- The Railway deploy log shows `migrate` finishing and gunicorn starting.
- `python manage.py preflight` has no `FAIL`.
- The GitHub Actions run on the branch is green.
- Log in as the Administrator, upload a signed form, redeploy, and confirm the file is
  still downloadable (proves the volume is attached).
- Send a password reset to a real address (proves email works).

## Email (password reset links)

Without email settings the system only prints emails to the server log, so a member who uses
"Forgot password" is told a link was sent but receives nothing. Railway's Hobby plan also blocks
SMTP, so use an email service over HTTPS:

1. Create a free account at brevo.com (300 emails a day) and, under Senders, add and verify the
   address FCU will send from (a Gmail address works; click the link Brevo emails to it).
2. Create an API key (SMTP & API, then API Keys).
3. In Railway, Variables: `BREVO_API_KEY` = the key, `DEFAULT_FROM_EMAIL` = `FCU <the verified address>`.
   Remove any `EMAIL_BACKEND` variable so the HTTPS sender is used. Redeploy.
4. Test: use Forgot password with a member's email. If nothing arrives, the deploy log shows
   "Brevo refused the email" with the reason.

Members need an email on their record (Members, Edit) for a reset link to be possible. Until email
works, the Administrator can give a temporary password from Users & Roles.

