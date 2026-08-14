"""Monthly invoice generation from append-only invoice line items."""

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum


class InvoiceGenerator:
    """Generates invoice for a single SACCO for a given billing month."""

    def generate(self, sacco, billing_month: date):
        """
        Generate one invoice for a SACCO and billing month.

        ``billing_month`` must be set to the first day of the month being
        invoiced, for example ``date(2026, 7, 1)`` for July 2026.
        """
        from billing.models import Invoice, InvoiceLineItem

        billing_month = billing_month.replace(day=1)

        with transaction.atomic():
            line_items = InvoiceLineItem.objects.select_for_update().filter(
                sacco=sacco,
                billing_month=billing_month,
                invoiced=False,
            )

            if not line_items.exists():
                return None

            total = (
                line_items.aggregate(total=Sum('platform_fee'))['total']
                or Decimal('0')
            )
            invoice_number = self._next_invoice_number(billing_month)
            line_items_count = line_items.count()

            invoice = Invoice.objects.create(
                sacco=sacco,
                invoice_number=invoice_number,
                billing_month=billing_month,
                total_amount=total,
                line_items_count=line_items_count,
                status='draft',
                due_date=date.today() + timedelta(days=7),
            )
            line_items.update(invoiced=True, invoice=invoice)

        return invoice

    def _next_invoice_number(self, billing_month: date) -> str:
        """Generate invoice numbers like SS-2026-07-001."""
        from billing.models import Invoice

        prefix = f'SS-{billing_month.year}-{billing_month.month:02d}'
        count = Invoice.objects.filter(
            invoice_number__startswith=prefix,
        ).count()
        return f'{prefix}-{count + 1:03d}'
