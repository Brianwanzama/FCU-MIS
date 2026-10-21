# Trying FCU-MIS out

Use a **copy with demo data** (your laptop or a staging service), never the live system:
`seed_demo` creates accounts with a known password and refuses to run in production.

## A. On your own computer (about 10 minutes)

You need Python 3.12 or newer and PostgreSQL.

```bash
git clone https://github.com/Brianwanzama/FCU-MIS.git && cd FCU-MIS
git checkout claude/lucid-fermi-c7scg1        # or main, once the pull request is merged

python3 -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

createdb fcu_mis                                       # or create the database in pgAdmin
cp .env.example .env                                   # then put your PostgreSQL password in DATABASE_URL

python manage.py migrate
python manage.py seed_demo                             # demo accounts, a running cycle, a few payments
python manage.py runserver
```

Open **http://127.0.0.1:8000** and log in.

| Log in as | Role | Password |
| --- | --- | --- |
| `demo_admin` | Administrator (FCU001) | `Demo-pass-2026!` |
| `demo_treasurer` | Treasurer (FCU002) | same |
| `demo_chair` | Chairperson (FCU003) | same |
| `demo_secretary` | Secretary (FCU004) | same |
| `demo_member` | Member (FCU005) | same |
| `demo_member2` | Member (FCU006) | same |

Use two browsers (or one normal and one private window) so you can be a member and an
officer at the same time. Password-reset and activation emails print in the terminal window.

## B. On Railway (a staging service)

Follow `docs/DEPLOYMENT.md`, then open the service's public address (Railway shows it under
Settings > Networking, like `https://<name>.up.railway.app`). Run `python manage.py preflight`
in a Railway shell. On a **staging** copy only, `python manage.py seed_demo --force` creates
the same demo accounts.

## What to try

**As `demo_member` (FCU005)**
1. *My Finances*: see deposits, emergency, available savings; switch cycles; download the statement as PDF and Excel.
2. Try opening another member's page by typing `/members/FCU001/`: you should get a "forbidden" page.
3. *Loans*: download both forms, upload a PDF as the signed Declaration, check "Can I apply?".
   Demo payments are part-paid, so you will see why a loan is refused; that is the system working.
4. *Investments*: see the portfolio and your indicative share. *Reports* should be refused.

**As `demo_treasurer`**
1. *Contributions* > Record Contribution; then Edit it (a reason is required) and request a Reverse.
2. *Investments* > New investment; record an investment and a market value update.
3. *Loan Register*; after a loan is approved, disburse it and record a repayment.

**As `demo_chair` or `demo_admin`**
1. *Contributions* > Reversal requests: approve the Treasurer's request (you cannot approve your own).
2. *Loan Applications*: open `demo_member`'s application and approve or reject it with a reason.
3. *Reports* > the cycle: view, print, download PDF and Excel.
4. *Cycles*: edit the dates, then Close: read the warnings and see what is blocked.
5. *Investments* > Profit distributions: draft, then approve with a resolution reference.

**A loan end to end** (as `demo_admin` do steps 1 to 3 to give `demo_member` enough deposits)
1. Record 600,000 of deposits (Monthly) for FCU005 and 3 months' worth up to date.
2. As `demo_member`: upload the signed Declaration, then apply (e.g. 300,000 for 3 months).
3. As `demo_secretary` or any officer: *Loans* > Signed declarations > Verify.
4. As `demo_chair`: approve. As `demo_treasurer`: disburse and record a repayment.

## Automatic checks

```bash
python manage.py test            # 265 tests, about two minutes
python manage.py preflight       # configuration check
python manage.py recover_overdue_loans --dry-run
```
