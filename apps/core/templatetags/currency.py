from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def ugx(value):
    """
    Format an amount as Ugandan shillings: 1250000 -> "UGX 1,250,000".

    The single formatter for every money figure in the MIS. Shillings have no
    minor unit in practice, so amounts are rounded to whole shillings.
    Empty or non-numeric values render as "UGX 0" rather than breaking a page.
    """

    try:
        amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        amount = Decimal("0")

    return f"UGX {amount:,}"
