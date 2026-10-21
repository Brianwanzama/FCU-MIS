"""
Loan application rules (Manual v3.0, section 4).

All the policy lives here, in one place, so the member's "can I apply?" panel,
the submission check and the approver's decision can never disagree.
"""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.auditlog.utils import log_action
from apps.contributions.services import contribution_arrears_to_date
from apps.cycles.services import get_active_cycle
from apps.members.models import Member
from apps.members.services import member_available_savings

from .models import Loan, LoanApplication, LoanInstallment, LoanRepayment, SignedUndertaking

ALLOWED_UPLOADS = {
    ".pdf": (b"%PDF",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG",),
}


class LoanError(ValidationError):
    """Raised when a loan operation breaks an FCU rule."""


def add_months(start, months):
    """The same day `months` later, or the month's last day if it has no such day."""

    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1

    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _whole(value):
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _require(actor, permission, action):
    if actor is None or not getattr(actor, permission, False):
        raise PermissionDenied(f"You do not have permission to {action}.")


# ============================================================
# Uploads
# ============================================================

def validate_upload(upload):
    """Only PDF, JPG or PNG files within the size limit, checked by content."""

    name = (upload.name or "").lower()
    extension = "." + name.rsplit(".", 1)[-1] if "." in name else ""

    if extension not in ALLOWED_UPLOADS:
        raise LoanError("Upload a PDF, JPG or PNG file.")

    limit = settings.FCU_UPLOAD_MAX_MB * 1024 * 1024

    if upload.size > limit:
        raise LoanError(f"The file is too large. The limit is {settings.FCU_UPLOAD_MAX_MB} MB.")

    head = upload.read(8)
    upload.seek(0)

    if not any(head.startswith(sig) for sig in ALLOWED_UPLOADS[extension]):
        raise LoanError("That file's contents do not match its type. Upload the scanned form as a PDF, JPG or PNG.")


# ============================================================
# Repayment schedule (Manual 4.5)
# ============================================================

def loan_schedule(principal, months, start_date, monthly_rate_percent=None):
    """
    Equal monthly instalments of principal AND interest (Manual 4.5(b)), with
    interest of `rate` per month on the outstanding balance (4.5(a)).

    Instalments are rounded to whole shillings; the last one is adjusted so the
    loan clears exactly. Returns a list of dicts, one per instalment.
    """

    rate = Decimal(str(
        settings.FCU_DEFAULT_LOAN_INTEREST_PERCENT
        if monthly_rate_percent is None
        else monthly_rate_percent
    )) / Decimal("100")

    principal = Decimal(principal)

    if rate == 0:
        instalment = _whole(principal / months)
    else:
        instalment = _whole(principal * rate / (1 - (1 + rate) ** (-months)))

    balance = principal
    rows = []

    for number in range(1, months + 1):
        interest = _whole(balance * rate)

        if number == months:
            principal_part = balance
            payment = principal_part + interest
        else:
            payment = instalment
            principal_part = payment - interest

        balance -= principal_part

        rows.append(
            {
                "number": number,
                "due_date": add_months(start_date, number),
                "instalment": payment,
                "principal": principal_part,
                "interest": interest,
                "balance": balance,
            }
        )

    return rows


def max_repayment_months(cycle, start_date):
    """
    The longest repayment period (at most 3 months) whose final instalment falls
    within the running cycle (Manual 4.5(b) and (d)). 0 if even one month does not fit.
    """

    for months in range(settings.FCU_MAX_LOAN_REPAYMENT_MONTHS, 0, -1):
        if add_months(start_date, months) <= cycle.end_date:
            return months

    return 0


# ============================================================
# Eligibility (Manual 4.3)
# ============================================================

def outstanding_loan_balance(member):
    """Unpaid principal and interest across the member's loans."""

    return sum(
        (loan.outstanding for loan in member.loans.exclude(status=Loan.Status.SETTLED)),
        Decimal("0"),
    )


def member_loan_summary(member):
    """Totals for the member's dashboard and statement."""

    loans = list(member.loans.all())

    return {
        "borrowed": sum((loan.principal for loan in loans), Decimal("0")),
        "repaid": sum((loan.total_paid for loan in loans), Decimal("0")),
        "balance": sum((loan.outstanding for loan in loans if loan.status != Loan.Status.SETTLED), Decimal("0")),
        "loans": loans,
    }


def loan_limit(member):
    """Manual 4.4: a percentage (120%) of accumulated Contributions, less what is already owed."""

    pool = member_available_savings(member)
    percent = Decimal(str(settings.FCU_LOAN_LIMIT_PERCENT))
    gross = _whole(pool * percent / Decimal("100"))

    return {
        "pool": pool,
        "percent": percent,
        "limit": max(gross - outstanding_loan_balance(member), Decimal("0")),
    }


def current_undertaking(member):
    """The undertaking to rely on: the newest verified one, else the newest pending one."""

    undertakings = member.undertakings.exclude(status=SignedUndertaking.Status.REJECTED)

    return (
        undertakings.filter(status=SignedUndertaking.Status.VERIFIED).first()
        or undertakings.first()
    )


def check_eligibility(
    member, amount=None, months=None, today=None, ignoring=None,
    require_verified=False, assume_undertaking=False,
):
    """
    Run the Manual 4.3 checks. Returns a dict with a list of `checks`
    (label, ok, blocking, detail) and `eligible`, which is True only when every
    BLOCKING check passes. Non-blocking checks are shown as warnings.

    `amount` and `months` are checked as well when given. `ignoring` is an
    application to leave out of the "already has one open" check (the one being
    decided). `require_verified` is used at approval: the undertaking must have
    been verified, not merely uploaded. `assume_undertaking` treats a declaration
    that is being uploaded together with the application as on file.
    """

    today = today or timezone.localdate()
    cycle = get_active_cycle()
    checks = []

    def add(key, label, ok, detail="", blocking=True):
        checks.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "blocking": blocking})

    add(
        "cycle",
        "A Financial Cycle is running",
        cycle is not None,
        "" if cycle else "Loans can only be applied for while a cycle is active.",
    )

    add(
        "active",
        "Active member (Manual 4.3(a))",
        member.status == Member.Status.ACTIVE,
        "" if member.status == Member.Status.ACTIVE
        else f"Your membership is {member.get_status_display()}. Inactive members cannot borrow (Manual 2.1(c)).",
    )

    undertaking = current_undertaking(member)
    if undertaking is None and assume_undertaking:
        undertaking_ok, detail = True, "Uploaded with this application, awaiting verification."
    elif undertaking is None:
        undertaking_ok, detail = False, "Upload your signed Member Declaration and Commitment Agreement."
    elif require_verified and undertaking.status != SignedUndertaking.Status.VERIFIED:
        undertaking_ok, detail = False, "The signed Declaration has not been verified yet."
    elif undertaking.status == SignedUndertaking.Status.VERIFIED:
        undertaking_ok, detail = True, "Verified."
    else:
        undertaking_ok, detail = True, "Uploaded, awaiting verification."
    add("undertaking", "Signed Member Declaration on file (Manual 9, 4.6)", undertaking_ok, detail)

    open_applications = member.loan_applications.filter(
        status__in=[LoanApplication.Status.SUBMITTED, LoanApplication.Status.APPROVED]
    )
    if ignoring is not None:
        open_applications = open_applications.exclude(pk=ignoring.pk)
    add(
        "open_application",
        "No other application awaiting a decision or payout",
        not open_applications.exists(),
        "You already have an application that has not been paid out yet." if open_applications.exists() else "",
    )

    owed = outstanding_loan_balance(member)
    add(
        "no_loan_owed",
        "No unpaid loan or overdue repayment (Good Standing)",
        owed == 0,
        f"You still owe UGX {owed:,.0f}." if owed else "",
    )

    limit_info = loan_limit(member)
    max_months = 0

    if cycle is not None:
        arrears = contribution_arrears_to_date(member, cycle, today)
        deposits_ok = arrears["deposit_arrears"] == 0
        add(
            "deposits_paid",
            "Contributions up to date (Manual 4.3(c))",
            deposits_ok,
            "" if deposits_ok else
            f"Overdue deposits: UGX {arrears['deposit_arrears']:,.0f} "
            f"({arrears['months_overdue']} month(s) past the payment window).",
        )
        add(
            "emergency_paid",
            "Emergency contributions up to date",
            arrears["emergency_arrears"] == 0,
            "" if arrears["emergency_arrears"] == 0 else
            f"Overdue emergency contributions: UGX {arrears['emergency_arrears']:,.0f}. "
            f"Approvers will see this.",
            blocking=False,
        )
        max_months = max_repayment_months(cycle, today)

    add(
        "limit",
        f"Contributions to borrow against (Manual 4.4: up to {limit_info['percent']:g}%)",
        limit_info["limit"] > 0,
        f"Accumulated deposits UGX {limit_info['pool']:,.0f}; you may borrow up to UGX {limit_info['limit']:,.0f}.",
    )

    if amount is not None:
        amount = Decimal(amount)
        within = 0 < amount <= limit_info["limit"]
        add(
            "amount",
            "Amount within your limit",
            within,
            "" if within else
            f"UGX {amount:,.0f} is above your limit of UGX {limit_info['limit']:,.0f}."
            if amount > 0 else "Enter an amount greater than zero.",
        )

    if months is not None:
        fits = 1 <= months <= max_months
        if max_months == 0 and cycle is not None:
            detail = "The current cycle ends too soon for any repayment period (Manual 4.5(d))."
        else:
            detail = (
                "" if fits else
                f"The longest period that finishes within the current cycle is {max_months} month(s)."
            )
        add("period", "Repayment period fits the cycle (Manual 4.5(b), (d))", fits, detail)

    return {
        "checks": checks,
        "eligible": all(c["ok"] for c in checks if c["blocking"]),
        "cycle": cycle,
        "undertaking": undertaking,
        "limit": limit_info["limit"],
        "pool": limit_info["pool"],
        "percent": limit_info["percent"],
        "max_months": max_months,
    }


def _snapshot_checks(result):
    return {
        "eligible": result["eligible"],
        "pool": str(result["pool"]),
        "limit": str(result["limit"]),
        "percent": str(result["percent"]),
        "max_months": result["max_months"],
        "checks": [
            {k: c[k] for k in ("key", "label", "ok", "blocking", "detail")} for c in result["checks"]
        ],
    }


# ============================================================
# Undertakings
# ============================================================

@transaction.atomic
def upload_undertaking(member, upload, actor):
    validate_upload(upload)

    undertaking = SignedUndertaking.objects.create(
        member=member,
        file=upload,
        original_name=upload.name[:255],
        uploaded_by=actor,
    )

    log_action(
        AuditLog.Action.CREATE,
        instance=undertaking,
        actor=actor,
        new_value={"event": "UNDERTAKING_UPLOADED", "member": member.member_code, "file": undertaking.original_name},
    )

    return undertaking


@transaction.atomic
def review_undertaking(undertaking, actor, verified, note=""):
    _require(actor, "can_verify_undertakings", "verify signed declarations")

    if undertaking.status != SignedUndertaking.Status.PENDING:
        raise LoanError("This declaration has already been reviewed.")

    note = (note or "").strip()

    if not verified and not note:
        raise LoanError("Say why the declaration is being rejected.")

    undertaking.status = (
        SignedUndertaking.Status.VERIFIED if verified else SignedUndertaking.Status.REJECTED
    )
    undertaking.reviewed_by = actor
    undertaking.reviewed_at = timezone.now()
    undertaking.review_note = note
    undertaking.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=undertaking,
        actor=actor,
        new_value={
            "event": "UNDERTAKING_VERIFIED" if verified else "UNDERTAKING_REJECTED",
            "member": undertaking.member.member_code,
            "note": note,
        },
    )

    return undertaking


# ============================================================
# Applications
# ============================================================

@transaction.atomic
def submit_application(member, amount, months, form_upload, actor, undertaking_upload=None):
    """
    Accept a loan application, but only from a member who passes Manual 4.3.
    The filled Loan Application Form is required; the signed Declaration is
    required too unless the member already has one on file.
    """

    validate_upload(form_upload)

    if undertaking_upload is not None:
        validate_upload(undertaking_upload)

    # An undertaking uploaded with the application counts as on file.
    result = check_eligibility(
        member, amount=amount, months=months,
        assume_undertaking=undertaking_upload is not None,
    )

    if not result["eligible"]:
        failed = [f"{c['label']}: {c['detail']}" if c["detail"] else c["label"]
                  for c in result["checks"] if c["blocking"] and not c["ok"]]
        raise LoanError(failed)

    if undertaking_upload is not None:
        undertaking = upload_undertaking(member, undertaking_upload, actor)
    else:
        undertaking = result["undertaking"]

    application = LoanApplication.objects.create(
        member=member,
        cycle=result["cycle"],
        amount_requested=amount,
        period_months=months,
        application_form=form_upload,
        application_form_name=form_upload.name[:255],
        undertaking=undertaking,
        eligibility_snapshot=_snapshot_checks(result),
    )

    log_action(
        AuditLog.Action.CREATE,
        instance=application,
        actor=actor,
        new_value={
            "event": "LOAN_APPLICATION_SUBMITTED",
            "member": member.member_code,
            "amount_requested": str(amount),
            "period_months": months,
            "limit": str(result["limit"]),
        },
    )

    return application


def _require_open(application):
    if application.status != LoanApplication.Status.SUBMITTED:
        raise LoanError("This application has already been decided.")


@transaction.atomic
def approve_application(application, actor, amount_approved=None, note=""):
    """
    Approve an application. Re-checks everything as it stands today, requires a
    verified Declaration, and fixes the interest and repayment schedule.
    """

    _require(actor, "can_approve_loans", "approve loans")
    _require_open(application)

    if actor.member_id is not None and actor.member_id == application.member_id:
        raise LoanError("You cannot approve your own loan application.")

    amount = Decimal(amount_approved if amount_approved is not None else application.amount_requested)

    if amount <= 0 or amount > application.amount_requested:
        raise LoanError("The approved amount must be above zero and no more than the amount requested.")

    application.member.refresh_from_db()  # status may have changed since it was loaded

    today = timezone.localdate()
    result = check_eligibility(
        application.member,
        amount=amount,
        months=application.period_months,
        today=today,
        ignoring=application,
        require_verified=True,
    )

    if not result["eligible"]:
        failed = [f"{c['label']}: {c['detail']}" if c["detail"] else c["label"]
                  for c in result["checks"] if c["blocking"] and not c["ok"]]
        raise LoanError(failed)

    schedule = loan_schedule(amount, application.period_months, today)

    application.status = LoanApplication.Status.APPROVED
    application.decided_by = actor
    application.decided_at = timezone.now()
    application.decision_note = (note or "").strip()
    application.amount_approved = amount
    application.interest_rate_percent = Decimal(str(settings.FCU_DEFAULT_LOAN_INTEREST_PERCENT))
    application.installment_amount = schedule[0]["instalment"]
    application.approved_schedule = [
        {k: (v.isoformat() if hasattr(v, "isoformat") else str(v)) for k, v in row.items()}
        for row in schedule
    ]
    application.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=application,
        actor=actor,
        new_value={
            "event": "LOAN_APPLICATION_APPROVED",
            "amount_approved": str(amount),
            "interest_rate_percent": str(application.interest_rate_percent),
            "period_months": application.period_months,
            "note": application.decision_note,
        },
    )

    return application


@transaction.atomic
def reject_application(application, actor, note):
    _require(actor, "can_approve_loans", "reject loans")
    _require_open(application)

    note = (note or "").strip()

    if not note:
        raise LoanError("Give the reason for rejecting the application. The member will see it.")

    application.status = LoanApplication.Status.REJECTED
    application.decided_by = actor
    application.decided_at = timezone.now()
    application.decision_note = note
    application.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=application,
        actor=actor,
        new_value={"event": "LOAN_APPLICATION_REJECTED", "note": note},
    )

    return application


@transaction.atomic
def withdraw_application(application, actor):
    """A member may withdraw their own application until it is decided."""

    if actor is None or actor.member_id != application.member_id:
        raise PermissionDenied("Only the applicant can withdraw an application.")

    _require_open(application)

    application.status = LoanApplication.Status.WITHDRAWN
    application.decided_at = timezone.now()
    application.save()

    log_action(
        AuditLog.Action.STATUS_CHANGE,
        instance=application,
        actor=actor,
        new_value={"event": "LOAN_APPLICATION_WITHDRAWN"},
    )

    return application


# ============================================================
# Disbursement
# ============================================================

@transaction.atomic
def disburse_loan(application, actor, method, reference="", disbursed_on=None):
    """
    Pay out an approved application and start the repayment schedule.

    The Treasurer (or Administrator) records the disbursement. Interest runs from
    the day the money leaves FCU, so the schedule is fixed here, from that date,
    and must still finish inside the running cycle (Manual 4.5(d)). No extension
    is possible afterwards (4.5(c)).
    """

    _require(actor, "has_financial_write_access", "disburse loans")

    application = LoanApplication.objects.select_for_update().get(pk=application.pk)

    if application.status != LoanApplication.Status.APPROVED:
        raise LoanError("Only an approved application can be disbursed.")

    if method not in LoanRepayment.Method.values:
        raise LoanError("Choose how the money was paid out.")

    today = timezone.localdate()
    disbursed_on = disbursed_on or today

    if disbursed_on > today:
        raise LoanError("The disbursement date cannot be in the future.")

    application.member.refresh_from_db()
    cycle = get_active_cycle()

    result = check_eligibility(
        application.member,
        amount=application.amount_approved,
        months=application.period_months,
        today=disbursed_on,
        ignoring=application,
        require_verified=True,
    )

    if not result["eligible"]:
        failed = [f"{c['label']}: {c['detail']}" if c["detail"] else c["label"]
                  for c in result["checks"] if c["blocking"] and not c["ok"]]
        raise LoanError(failed)

    schedule = loan_schedule(
        application.amount_approved, application.period_months, disbursed_on,
        application.interest_rate_percent,
    )

    loan = Loan.objects.create(
        application=application,
        member=application.member,
        cycle=cycle,
        principal=application.amount_approved,
        interest_rate_percent=application.interest_rate_percent,
        period_months=application.period_months,
        disbursed_on=disbursed_on,
        disbursed_by=actor,
        disbursement_method=method,
        disbursement_reference=(reference or "").strip(),
    )

    LoanInstallment.objects.bulk_create(
        LoanInstallment(
            loan=loan,
            number=row["number"],
            due_date=row["due_date"],
            principal_due=row["principal"],
            interest_due=row["interest"],
            amount_due=row["instalment"],
        )
        for row in schedule
    )

    application.status = LoanApplication.Status.DISBURSED
    application.save(update_fields=["status"])

    log_action(
        AuditLog.Action.CREATE,
        instance=loan,
        actor=actor,
        new_value={
            "event": "LOAN_DISBURSED",
            "member": loan.member.member_code,
            "principal": str(loan.principal),
            "interest_rate_percent": str(loan.interest_rate_percent),
            "period_months": loan.period_months,
            "disbursed_on": disbursed_on.isoformat(),
            "method": method,
            "reference": loan.disbursement_reference,
            "final_due_date": schedule[-1]["due_date"].isoformat(),
        },
    )

    return loan


# ============================================================
# Repayments
# ============================================================

def rebuild_allocation(loan):
    """
    Spread the loan's live repayments over its instalments, oldest first, and
    update the loan's status. Replaying from scratch means a reversal can never
    leave the instalments out of step with the repayment records.
    """

    instalments = list(loan.installments.select_for_update().order_by("number"))

    for instalment in instalments:
        instalment.amount_paid = Decimal("0")

    live = loan.repayments.filter(reversed_at__isnull=True).order_by("payment_date", "id")

    for repayment in live:
        left = repayment.amount

        for instalment in instalments:
            if left <= 0:
                break

            take = min(instalment.remaining, left)
            instalment.amount_paid += take
            left -= take

    for instalment in instalments:
        instalment.save(update_fields=["amount_paid"])

    outstanding = sum((i.remaining for i in instalments), Decimal("0"))
    today = timezone.localdate()

    if outstanding == 0:
        loan.status = Loan.Status.SETTLED
        loan.settled_on = loan.settled_on or today
    else:
        loan.settled_on = None
        loan.status = Loan.Status.DEFAULTED if loan.defaulted_on else Loan.Status.ACTIVE

    loan.save(update_fields=["status", "settled_on"])

    return outstanding


@transaction.atomic
def record_repayment(loan, amount, payment_date, method, actor, reference=""):
    _require(actor, "has_financial_write_access", "record loan repayments")

    loan = Loan.objects.select_for_update().get(pk=loan.pk)

    if loan.status == Loan.Status.SETTLED:
        raise LoanError("This loan has already been settled.")

    amount = Decimal(amount)

    if amount <= 0:
        raise LoanError("A repayment must be greater than zero.")

    if method not in LoanRepayment.Method.values:
        raise LoanError("Choose how the money was paid.")

    if payment_date > timezone.localdate():
        raise LoanError("A repayment cannot be recorded as paid in the future.")

    if payment_date < loan.disbursed_on:
        raise LoanError("A repayment cannot be dated before the loan was disbursed.")

    owed = loan.outstanding

    if amount > owed:
        raise LoanError(f"Only UGX {owed:,.0f} is still owed on this loan.")

    repayment = LoanRepayment.objects.create(
        loan=loan,
        kind=LoanRepayment.Kind.PAYMENT,
        amount=amount,
        payment_date=payment_date,
        payment_method=method,
        reference=(reference or "").strip(),
        cycle=get_active_cycle() or loan.cycle,
        recorded_by=actor,
    )

    remaining = rebuild_allocation(loan)

    log_action(
        AuditLog.Action.CREATE,
        instance=repayment,
        actor=actor,
        new_value={
            "event": "LOAN_REPAYMENT_RECORDED",
            "loan": loan.reference,
            "amount": str(amount),
            "outstanding_after": str(remaining),
            "method": method,
            "reference": repayment.reference,
        },
    )

    return repayment


@transaction.atomic
def reverse_repayment(repayment, actor, reason):
    """
    Reverse a repayment or a recovery. Needs the Administrator or Chairperson,
    and never the person who recorded it. The record stays, marked reversed, and
    the loan (and, for a recovery, the member's savings) is restored.
    """

    _require(actor, "can_approve_loans", "reverse loan repayments")

    repayment = LoanRepayment.objects.select_for_update().select_related("loan").get(pk=repayment.pk)

    if repayment.reversed_at is not None:
        raise LoanError("This repayment has already been reversed.")

    if repayment.recorded_by_id is not None and repayment.recorded_by_id == actor.pk:
        raise LoanError("A repayment must be reversed by someone other than the person who recorded it.")

    reason = (reason or "").strip()

    if not reason:
        raise LoanError("Give a reason for the reversal.")

    repayment.reversed_at = timezone.now()
    repayment.reversed_by = actor
    repayment.reversal_reason = reason
    repayment.save()

    loan = repayment.loan
    remaining = rebuild_allocation(loan)

    log_action(
        AuditLog.Action.DELETE,
        instance=repayment,
        actor=actor,
        new_value={
            "event": "LOAN_REPAYMENT_REVERSED",
            "loan": loan.reference,
            "kind": repayment.kind,
            "amount": str(repayment.amount),
            "reason": reason,
            "outstanding_after": str(remaining),
        },
    )

    if repayment.kind == LoanRepayment.Kind.SAVINGS_OFFSET:
        from apps.members.services import reassess_after_loan_recovery

        reassess_after_loan_recovery(loan.member, actor=actor)

    return repayment


# ============================================================
# Overdue loans and automatic recovery (Manual 4.6, 4.7)
# ============================================================

def overdue_loans(today=None):
    """
    Loans whose agreed repayment period has ended with money still owed.

    The Manual treats failing to repay "within the period agreed at approval" as
    default (4.7(a)) and authorises recovery then (4.6(a)). A missed instalment on
    its own makes a loan Overdue, which is shown, but does not trigger recovery.
    """

    today = today or timezone.localdate()
    candidates = (
        Loan.objects.exclude(status=Loan.Status.SETTLED)
        .select_related("member", "application")
        .prefetch_related("installments", "repayments")
    )

    return [
        loan for loan in candidates
        if loan.final_due_date and loan.final_due_date < today and loan.outstanding > 0
    ]


def recovery_preview(loan):
    """What a recovery would take today, without taking it."""

    savings = max(member_available_savings(loan.member), Decimal("0"))
    owed = loan.outstanding

    return {
        "outstanding": owed,
        "savings": savings,
        "recoverable": min(owed, savings),
        "shortfall": max(owed - savings, Decimal("0")),
    }


def recovery_enabled():
    return bool(settings.FCU_LOAN_AUTO_RECOVERY_ENABLED)


@transaction.atomic
def recover_loan_from_savings(loan, actor=None, today=None):
    """
    Recover an overdue loan from the member's savings.

    Takes the smaller of what is owed (principal + interest) and the member's
    available deposits across all cycles. Emergency contributions are never
    touched, and the member's original contributions are not edited: the recovery
    is its own SAVINGS_OFFSET transaction linked to the loan. If savings fall
    short, the rest of the debt stays outstanding.

    Safe to run repeatedly: it locks the loan, works from live balances, and the
    database allows only one live recovery per loan per day.
    """

    today = today or timezone.localdate()
    loan = Loan.objects.select_for_update().select_related("member").get(pk=loan.pk)

    outcome = {"loan": loan, "recovered": Decimal("0"), "skipped": ""}

    if loan.status == Loan.Status.SETTLED or loan.outstanding <= 0:
        outcome["skipped"] = "Nothing is owed."
        return outcome

    if not (loan.final_due_date and loan.final_due_date < today):
        outcome["skipped"] = "The agreed repayment period has not ended."
        return outcome

    if loan.repayments.filter(
        kind=LoanRepayment.Kind.SAVINGS_OFFSET, recovery_date=today, reversed_at__isnull=True
    ).exists():
        outcome["skipped"] = "Already recovered today."
        return outcome

    if loan.defaulted_on is None:
        loan.defaulted_on = today
        loan.status = Loan.Status.DEFAULTED
        loan.save(update_fields=["defaulted_on", "status"])

        log_action(
            AuditLog.Action.STATUS_CHANGE,
            instance=loan,
            actor=actor,
            new_value={
                "event": "LOAN_DEFAULTED",
                "final_due_date": loan.final_due_date.isoformat(),
                "outstanding": str(loan.outstanding),
            },
        )

    preview = recovery_preview(loan)
    outcome["preview"] = preview

    if preview["recoverable"] <= 0:
        outcome["skipped"] = "The member has no savings available to recover from."
        return outcome

    repayment = LoanRepayment.objects.create(
        loan=loan,
        kind=LoanRepayment.Kind.SAVINGS_OFFSET,
        amount=preview["recoverable"],
        payment_date=today,
        recovery_date=today,
        cycle=get_active_cycle() or loan.cycle,
        recorded_by=actor,
    )

    remaining = rebuild_allocation(loan)
    loan.refresh_from_db()

    log_action(
        AuditLog.Action.CREATE,
        instance=repayment,
        actor=actor,
        new_value={
            "event": "LOAN_RECOVERED_FROM_SAVINGS",
            "loan": loan.reference,
            "member": loan.member.member_code,
            "outstanding_before": str(preview["outstanding"]),
            "savings_before": str(preview["savings"]),
            "recovered": str(repayment.amount),
            "outstanding_after": str(remaining),
            "savings_after": str(preview["savings"] - repayment.amount),
            "policy": settings.FCU_LOAN_RECOVERY_POLICY_REF,
            "automatic": actor is None,
        },
    )

    from apps.members.services import reassess_after_loan_recovery

    reassess_after_loan_recovery(loan.member, actor=actor)

    outcome["recovered"] = repayment.amount
    outcome["outstanding_after"] = remaining

    return outcome


def run_recovery(actor=None, today=None, dry_run=False):
    """
    Check every overdue loan and recover what can be recovered. This is what the
    scheduled job runs, with nobody logged in.

    Real recoveries only happen when FCU_LOAN_AUTO_RECOVERY_ENABLED is on, i.e.
    under an approved recovery policy. A dry run is always allowed and changes
    nothing.
    """

    if not dry_run and not recovery_enabled():
        raise LoanError(
            "Automatic loan recovery is not enabled. Set FCU_LOAN_AUTO_RECOVERY_ENABLED "
            "once FCU has approved the recovery policy."
        )

    results = []

    for loan in overdue_loans(today):
        if dry_run:
            results.append({"loan": loan, "preview": recovery_preview(loan), "recovered": Decimal("0"), "skipped": "Dry run."})
        else:
            results.append(recover_loan_from_savings(loan, actor=actor, today=today))

    return results
