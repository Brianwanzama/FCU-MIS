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

from .models import LoanApplication, SignedUndertaking

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
    """Unpaid loan principal and interest. Disbursed loans arrive with the next phase."""

    return Decimal("0")


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

    open_applications = member.loan_applications.filter(status=LoanApplication.Status.SUBMITTED)
    if ignoring is not None:
        open_applications = open_applications.exclude(pk=ignoring.pk)
    add(
        "open_application",
        "No other application awaiting a decision",
        not open_applications.exists(),
        "You already have an application awaiting a decision." if open_applications.exists() else "",
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
