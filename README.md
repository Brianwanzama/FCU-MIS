# FCU-MIS — Financial Cycle Unit Management Information System

Phase 0 (project scaffolding) + Phase 1 (Members & Auth) of the roadmap in
`FCU-MIS_SRS_v1.0.md`. Verified end-to-end against a real PostgreSQL database.

## What's implemented so far

- Django project with environment-split settings (`config/settings/{base,dev,prod}.py`)
- PostgreSQL as the only supported database, configured via `DATABASE_URL`
- One local app per SRS domain module under `apps/` — most are still skeletons
  (models arrive in their own roadmap phase); live in Phase 1 are:
  - `apps.members` — the `Member` model, `member_code` (FCU0xx) auto-generation, seed data for FCU001–FCU019
  - `apps.accounts` — custom `User` model (RBAC role + link to Member), closed-membership account activation, login/logout, password reset, admin-only user/role management
  - `apps.documents` — versioned Loan Application Form / Member Declaration templates, downloadable by any logged-in user
  - `apps.auditlog` — append-only audit trail; logs are wired for logins, account activation, and role changes today, and every future phase's financial writes will call the same `log_action()` helper
  - `apps.core` — FCU-branded base template, navigation, role-aware dashboard shell
- RBAC enforced server-side (`apps/accounts/permissions.py`) — verified with an actual 403 for a plain Member hitting an Administrator-only page, not just a hidden nav link

## Known data gaps to close before go-live

- **Member emails**: 13 of 19 members now have real emails (SRS v1.1 Appendix A).
  The remaining 6 — FCU003, FCU005, FCU009, FCU010, FCU015, FCU017 — are stored
  with a genuinely blank email (not a fake placeholder) and **cannot activate an
  account until an Administrator adds their real email** via Member Management.
- **Join dates**: no per-member join date was supplied, so all 19 seeded members
  default to `2023-07-01` (FCU's founding month). Correct individually if the real
  dates matter to you.

## Local setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# create a Postgres database matching .env's DATABASE_URL (or edit .env to match yours)
createdb fcu_mis

cp .env.example .env   # then edit DJANGO_SECRET_KEY at minimum

python manage.py migrate
python manage.py createsuperuser   # technical break-glass account only — see note below
python manage.py runserver
```

Then visit `http://127.0.0.1:8000/accounts/activate/` and activate FCU001 or FCU004
to get a real Administrator account tied to a Member (this is how the system is
meant to be used day-to-day — the Django `createsuperuser` account is only a
technical fallback for initial setup, not a normal login path).

## Document templates

The two governing documents (Loan Application Form, Member Declaration &
Commitment Agreement) are loaded as the initial `DocumentTemplate` records,
visible at `/documents/` once logged in. Uploading a replacement version is done
via Django Admin (Administrators only) → Document Templates.

## Running tests / re-verifying the activation flow

The activation → RBAC → audit-log chain was verified with Django's test client
during development (member activation, duplicate-activation rejection, wrong-email
rejection, role-based 403, document download). A proper `pytest`/`TestCase` suite
is recommended before Phase 2 — flag if you'd like that added now rather than later.

## Next phase

The SRS was revised to v1.1 (General Ledger, Financial Position, Cycle Locking,
Member/Cycle Summary tables, Financial Year, Dashboard specs, Search, Data Import
strategy, expanded Member Statement, performance via incremental summary refresh)
before any further code was written, per the client's request. Phase 2 —
Financial Year & Cycle management, including Cycle Locking — is next, per the
revised roadmap in `FCU-MIS_SRS_v1.1.md`. Awaiting go-ahead.
