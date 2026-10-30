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

## Business rules confirmed by FCU

- **Targets:** per member, deposits UGX 600,000 and emergency UGX 30,000 over a
  6-month cycle, reported separately. Unit targets = eligible (Active) members x those.
- **Mid-cycle joiners** owe the full cycle amount.
- **Member status** is assessed when a cycle closes: a member who did not meet the
  cycle's target is Inactive for the next cycle, and Active only if they completed
  the previous one. The target is the DEPOSIT target only; emergency contributions
  are not part of it. Ordinary payments never change status mid-cycle, and a
  Withdrawn member is never reactivated automatically.
- **Paying old arrears:** closed cycles stay locked, so arrears are paid as a
  contribution recorded in the active cycle with "Settles arrears of cycle" set. It
  counts towards the closed cycle's target (not the active cycle's), cannot exceed
  the arrears owed, and updates the member's status automatically: Active once the
  most recent closed cycle is fully paid, Inactive again if that payment is reversed.
- **Reopening a cycle** leaves the statuses set at its first close as they were; they
  are recalculated when it is closed again.
- **Loans (Phase 5/6, not built yet):** a loan can never exceed the member's
  previous contributions. Recovery may use all of the member's previous deposits
  (across cycles); emergency contributions are excluded and protected.

## Editing and reversing contributions

| Action | Administrator | Treasurer | Chairperson | Member |
| --- | --- | --- | --- | --- |
| View all contributions | yes | yes | yes | no |
| Record contributions | yes | yes | no | no |
| Edit (reason required) | yes | yes | yes | no |
| Request a reversal | yes | yes | yes | no |
| Approve / reject a reversal | yes | no | yes | no |

- A **reversal** is requested with a reason and takes effect only when someone *other
  than the requester* approves it. The payment then stops counting towards balances but
  stays in the register and the audit history. A wrongly entered payment can also simply
  be **deleted** (Delete button): the Treasurer, Chairperson or Administrator gives a
  reason, the entry leaves the register and every balance, and a full copy of it, with
  who deleted it, when and why, stays in the audit log. Closed-cycle payments cannot be
  deleted until the cycle is reopened.
- **Quick amounts:** the entry form has a "Quick amount" list that follows the payment type
  (deposits in UGX 100,000 steps up to 600,000; emergency in UGX 5,000 steps up to 30,000).
  The amount box stays editable for part payments.
- Every edit records old value, new value, user, time and reason; balances recalculate.
- Contributions in a closed cycle cannot be edited or reversed until an Administrator
  reopens the cycle. A payment with a reversal pending cannot be edited.
- Pages: `/contributions/<id>/` (detail + history), `/contributions/<id>/edit/`,
  `/contributions/<id>/reverse/`, `/contributions/reversals/`.
- Run `python manage.py migrate` after pulling: this adds migration `0003`.

## Cycle management

- **Who was eligible:** each cycle keeps its own membership record
  (`CycleMembership`). Activating a cycle records every Active member; a member who
  becomes Active mid-cycle (new joiner, or cleared arrears) is added and owes the
  full amount; a member who withdraws gets a leave date and stops counting. A cycle's
  unit targets use this record, so they no longer change when members later join or
  leave. An Upcoming cycle projects today's Active headcount. Cycles that were already
  running when this was added were back-filled from the members Active at that time.
- **Editing dates:** start date and duration can be changed while a cycle is Upcoming
  or Active. Targets recalculate, the change is audited (old and new dates), and any
  payment whose month now falls outside the cycle is flagged on the cycle page and
  in the close warnings for review.
- **Closing:** a cycle cannot be closed while a reversal request is pending (hard
  block). The close screen also warns about outstanding deposits, outstanding
  emergency contributions, flagged payments and the members who will become Inactive.
  Outstanding loans will be added when the Loans module exists.
- Run `python manage.py migrate` after pulling: adds migrations `cycles.0002` and `0003`.

## Member financial history and statements

- `/members/<code>/` is the member's financial dashboard with a **cycle selector**
  (every cycle that has begun). It shows deposits and emergency (target, paid,
  outstanding), available savings, loan balance and the payment ledger for the
  selected cycle. `/members/me/` ("My Finances" in the menu) opens a signed-in
  member's own page.
- **Access:** a member can open only their own pages (403 otherwise). The
  Administrator, Treasurer and Chairperson can open anyone's. The Secretary can see
  the member register but not other members' finances.
- **Statements:** `/members/<code>/statement/?cycle=<id>` (printable) and
  `/members/<code>/statement.pdf?cycle=<id>` (PDF). The ledger shows a running
  deposits total; reversed payments and payments that count towards another cycle
  are shown in grey and are not counted.
- **Available savings** = deposits paid across all cycles, reversed payments and
  emergency contributions excluded. Loan balances will be deducted once Loans exist.

## Loan applications (Manual v3.0, section 4)

Members apply from **Loans** in the menu (`/loans/`):

1. Download the **Loan Application Form** and the **Member Declaration and Commitment
   Agreement** (the consent to recovery from contributions, Manual 4.6) from the page.
2. Fill in and sign them, scan or photograph them, and upload them (PDF, JPG or PNG,
   up to 5 MB, checked by content). The signed Declaration is uploaded once and reused.
3. The system checks Manual 4.3 before accepting the application: Active member,
   declaration on file, no other open application, deposits up to date (month due by the
   10th of the next month), amount within **100% of accumulated deposits** (emergency
   excluded; FCU's decision, Manual 4.4 and the form still say 120%), repayment of **at most 3 months that finishes inside the
   running cycle** (4.5). Overdue emergency contributions are shown as a warning only.
4. The Administrator or Chairperson approves or rejects (never their own application).
   Approval needs a **verified** Declaration, re-runs the checks, and fixes the schedule:
   5% a month on the outstanding balance, equal monthly instalments of principal and
   interest. The member sees the status, any reason for rejection, and the schedule.

Staff pages: `/loans/applications/` (Administrator, Treasurer, Chairperson),
`/loans/declarations/` (verification: Administrator, Treasurer, Chairperson, Secretary).
No guarantor is needed (4.2(b)).

**Uploaded files are private.** They are stored outside `MEDIA_ROOT` in
`PRIVATE_MEDIA_ROOT` (env `FCU_PRIVATE_MEDIA_ROOT`, default `./private_media`) and are
handed out only by views that check the requester. On Railway, mount a persistent volume
and point `FCU_PRIVATE_MEDIA_ROOT` at it, or uploads are lost on every deploy.

Settings: `FCU_LOAN_LIMIT_PERCENT` (default 100), `FCU_DEFAULT_LOAN_INTEREST_PERCENT`
(5, per month), `FCU_UPLOAD_MAX_MB` (5). The old `FCU_DEFAULT_MAX_LOAN_*` settings are gone.
Run `python manage.py migrate`: adds `loans.0001` and `contributions.0004`.

### Disbursement, repayments and recovery

- **Disbursement:** the Treasurer or Administrator records the payout of an approved
  application (method, reference, date). That creates the loan and fixes the schedule
  from the payout date (5% a month on the outstanding balance, equal monthly
  instalments); it must still finish inside the running cycle. A member cannot borrow
  again while a loan is unpaid, and the loan limit is reduced by what is owed.
- **Repayments:** the Treasurer or Administrator records them; they clear the oldest
  instalment first. Overpaying is refused. Reversing a repayment (or a recovery) needs
  the Administrator or Chairperson, never the person who recorded it, with a reason;
  the record stays, marked reversed, and the balance is restored.
- **Statuses:** Current, Overdue (an instalment is late), Defaulted (the agreed period
  ended with money owed, Manual 4.7) and Settled. Pages: `/loans/loan/<id>/`,
  `/loans/register/`; members see their own loans on `/loans/`.
- **Automatic recovery (Manual 4.6):** when a loan's agreed period has ended with money
  still owed, FCU recovers principal + interest from the member's **deposits across all
  cycles; emergency contributions are never touched**. If savings fall short, the rest
  stays outstanding and later deposits are recovered on later days. The recovery is a
  separate "savings offset" transaction linked to the loan: the original contributions
  are not edited, and statements show both. Each recovery is audited with amounts before
  and after and the policy reference. It cannot run twice in a day (locked, and enforced
  by the database).
- **Effect on status (4.6(d)):** if the recovery leaves the member below the deposits
  required for the cycle to date, they become Inactive ("Loan recovered from
  contributions") until they pay back up, then Active again automatically.
- **Automatic daily check:** recovery is **on by default** (`FCU_LOAN_AUTO_RECOVERY_ENABLED`,
  set it to `False` to switch it off; `FCU_LOAN_RECOVERY_POLICY_REF` names the authority
  written to the audit log). The check runs by itself the first time anyone signs in each
  day, so no scheduler is needed, and it is recorded once per day (`RecoveryRun`), however
  many servers or visits there are. `python manage.py recover_overdue_loans` (Railway cron
  or similar) does the same work and is still fine to schedule; `--dry-run` only reports.
  Disable the on-visit trigger with `FCU_LOAN_RECOVERY_ON_REQUEST=False`.
- **Loans made outside the system:** the Treasurer, Chairperson or Administrator adds a
  loan from **Loan register -> Add loan made outside the system**: member, amount, period,
  interest, date given and anything already repaid. The system builds the schedule, so the
  balance, overdue status, the member's loan limit and automatic recovery all work for it.
- **Deleting:** wrongly entered repayments, recoveries, loans, applications and declarations
  can be deleted by the Treasurer, Chairperson or Administrator with a reason. Deleting a
  loan reopens its application as Approved; deleting a recovery gives the savings back. A
  copy of everything deleted stays in the audit log.
- Run `python manage.py migrate`: adds `loans.0002` and `members.0005`.

## Investments and profit distribution (Manual v3.0, sections 7 and 8)

- **Investments** (`/investments/`, "Investments" in the menu): unit trusts, microfinance
  ventures and real estate, registered in FCU's name. The Treasurer or Administrator
  records money invested, money returned, earnings received, fees and market value
  updates. Reversing a transaction needs the Administrator or Chairperson (not the
  recorder) and a reason. Transactions, whole investments and profit distributions can
  also be **deleted** (reason required, copy kept in the audit log); the position is
  recalculated.
- **Position** per investment: invested, worth now (latest valuation, or what is still
  in it), earnings, and total return. **Every member** sees the portfolio totals and
  their own indicative share (their accumulated deposits as a share of all members'
  deposits, applied to the portfolio's value); only officers see the transactions.
- **Profit distribution (bonus):** the Treasurer drafts a distribution; the Chairperson or
  Administrator (not the drafter) approves it with the **General Meeting resolution
  reference**; the Treasurer marks it paid. Shares are in proportion to each member's
  accumulated deposits (after loan recoveries) and go only to **Active members in Good
  Standing** (no overdue contributions, no overdue or defaulted loan); Inactive and
  Withdrawn members get nothing and their share is spread over the eligible members.
  Whole shillings, adding up exactly. Shares are recalculated at approval and then fixed.
- Member dashboard: net position = deposits + bonuses received - loan balance. A share
  of the investments is shown for information but not added (it is funded by the deposits).
- Run `python manage.py migrate`: adds `unittrust.0001`.

## Accounts, roles and passwords

- **More than one role:** on *Users & Roles* the Administrator picks a main role and ticks
  any "also acts as" roles (for example a Chairperson who is also Treasurer). A person
  gets the permissions of every role they hold.
- **Sign in as a member:** the Administrator can sign in as any other user to see exactly
  what they see ("Sign in as this user"). A banner shows the Administrator is in someone
  else's account with a "Back to my account" button; it is audited, and cannot be used
  to enter another Administrator's account.
- **Passwords:** every user can change their own password (*Change password* in the menu).
  The Administrator can *Set a temporary password* (shown once, works without email) or
  *Ask to change password* for one user or for everyone; flagged users must choose a new
  password before they can use the system.
- **Delete everywhere:** contributions, loan records, investments, profit distributions,
  documents and members (a member with no financial history) can be deleted; a copy goes
  to the audit log. Cycles already had a guarded delete.
- Run `python manage.py migrate`: adds `accounts.0005` and `loans.0003`.

## Reports and Excel exports (Manual 10.2)

`/reports/` ("Reports" in the menu) for the Administrator, Treasurer and Chairperson.
A cycle is six months, so each cycle's report is the **bi-annual report**: contributions
(deposits and emergency against target), a row per member, loans disbursed in the cycle
(repaid, recovered, interest earned, outstanding), investments and profit shared, and the
financial position. View it, print it, or download **PDF** or **Excel** (Summary, Members,
Loans, Investments sheets). Also exportable to Excel: all contributions (filter by cycle),
loans with repayments, investments with transactions, and each member's statement.

- Excel text is protected against formula injection (`=`, `+`, `-`, `@` are kept as text).
- **Derived cash** is worked out from every recorded movement of money. Expenses and
  membership fees are not recorded yet, so check it against the bank and cash balances.
- Interest earned counts what is paid as interest first within each instalment.

## Charts

Interactive charts (Chart.js 4.4.1, bundled in `static/vendor/`, so no outside CDN is needed):

- **Dashboard (officers):** deposits collected each month against the monthly target, and a ring of who
  has met the deposit target. **Members** see their own deposits each month instead.
- **Cycle report:** the same two, plus deposits paid by each member (click a bar to open the member) and
  where the investments are.
- **Investments:** where the money is, and the total return on each investment.
- **Member page:** deposits paid each month against the monthly savings.

Hover for exact figures, click the legend to hide a series. Every chart has a **View as table** fallback
with the same numbers. Colours are the validated blue, orange and aqua series; the target ring uses the
fixed status colours with a symbol and a label so it never relies on colour alone. Charts are hidden when
printing. The data comes from `apps/reports/charts.py`, from the same records as the reports.

## Trying it out

See **docs/TESTING.md**: run it on your computer, load demo logins with `python manage.py seed_demo`, and a walkthrough per role.

## Deployment

See **docs/DEPLOYMENT.md** (Railway) or **docs/DEPLOY_DROPLET.md** (DigitalOcean Droplet) (Railway setup, variables, first deployment, backups, enabling loan
recovery). `python manage.py preflight` checks an installation; `GET /healthz/` is the
health check; CI runs on every push (`.github/workflows/ci.yml`).

## Next phase

The SRS was revised to v1.1 (General Ledger, Financial Position, Cycle Locking,
Member/Cycle Summary tables, Financial Year, Dashboard specs, Search, Data Import
strategy, expanded Member Statement, performance via incremental summary refresh)
before any further code was written, per the client's request. Phase 2 —
Financial Year & Cycle management, including Cycle Locking — is next, per the
revised roadmap in `FCU-MIS_SRS_v1.1.md`. Awaiting go-ahead.
