"""Billing service helpers for platform fees and monthly SACCO invoices."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import csv
from io import StringIO

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import MonthlySaccoInvoice, PlatformRevenue
from payments.models import PlatformFee, Transaction
from saccomanagement.models import Role


TRANSACTION_FEE_RATE = Decimal('0.02')


def record_transaction_fee(txn, sacco):
    """Apply and persist 2% platform fee for a completed transaction."""
    if txn.status != Transaction.Status.COMPLETED:
        return None

    existing_fee = PlatformFee.objects.filter(
        transaction=txn,
        fee_type=PlatformFee.FeeType.TRANSACTION_PCT,
    ).first()
    if existing_fee:
        return existing_fee

    fee_amount = (
        Decimal(txn.amount) * TRANSACTION_FEE_RATE
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    with db_transaction.atomic():
        txn.fee_amount = fee_amount
        txn.save(update_fields=['fee_amount', 'updated_at'])

        platform_fee = PlatformFee.objects.create(
            transaction=txn,
            fee_type=PlatformFee.FeeType.TRANSACTION_PCT,
            amount=fee_amount,
        )
        PlatformRevenue.objects.create(
            sacco=sacco,
            transaction=txn,
            revenue_type=PlatformRevenue.RevenueType.TRANSACTION_FEE,
            amount=fee_amount,
            currency=txn.currency,
            description='2% platform transaction fee',
        )

    return platform_fee


def previous_month_period(reference_date=None):
    """Return (start_date, end_date) for the previous calendar month."""
    if reference_date is None:
        reference_date = timezone.localdate()
    first_day_current_month = reference_date.replace(day=1)
    last_day_previous_month = first_day_current_month - timedelta(days=1)
    first_day_previous_month = last_day_previous_month.replace(day=1)
    return first_day_previous_month, last_day_previous_month


def generate_monthly_sacco_invoice(sacco, period_start, period_end):
    """Create or update monthly invoice for one SACCO based on platform fees."""
    fee_queryset = PlatformFee.objects.filter(
        transaction__status=Transaction.Status.COMPLETED,
        transaction__user__membership__sacco=sacco,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end,
    ).distinct()
    total_fee = fee_queryset.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    tx_count = fee_queryset.count()
    total_transacted_amount = (
        fee_queryset.aggregate(total=Sum('transaction__amount'))['total']
        or Decimal('0.00')
    )

    report_payload = {
        'period_start': str(period_start),
        'period_end': str(period_end),
        'transaction_count': tx_count,
        'total_transacted_amount': str(total_transacted_amount),
        'fee_rate': '2%',
        'amount_due': str(total_fee),
        'payment_account_name': getattr(
            settings,
            'BILLING_ACCOUNT_NAME',
            'SaccoSphere Ltd',
        ),
        'payment_account_number': getattr(
            settings,
            'BILLING_ACCOUNT_NUMBER',
            'N/A',
        ),
        'payment_paybill': getattr(settings, 'BILLING_PAYBILL', 'N/A'),
    }

    due_date = period_end + timedelta(days=14)
    invoice, _ = MonthlySaccoInvoice.objects.update_or_create(
        sacco=sacco,
        period_start=period_start,
        period_end=period_end,
        defaults={
            'amount_due': total_fee,
            'report_payload': report_payload,
            'due_date': due_date,
            'status': MonthlySaccoInvoice.Status.GENERATED,
        },
    )
    return invoice


def send_invoice_to_sacco(invoice):
    """Email monthly invoice summary to SACCO contacts and admin users."""
    recipients = set()
    if invoice.sacco.email:
        recipients.add(invoice.sacco.email)

    admin_emails = Role.objects.filter(
        sacco=invoice.sacco,
        name=Role.SACCO_ADMIN,
    ).select_related('user').values_list('user__email', flat=True)
    recipients.update([email for email in admin_emails if email])

    if not recipients:
        return False

    payload = invoice.report_payload
    message = (
        f"Sacco: {invoice.sacco.name}\n"
        f"Billing period: {payload['period_start']} to {payload['period_end']}\n"
        f"Transactions billed: {payload['transaction_count']}\n"
        f"Total transacted amount: KES {payload['total_transacted_amount']}\n"
        f"Fee rate: {payload['fee_rate']}\n"
        f"Amount due: KES {payload['amount_due']}\n\n"
        f"Payment details:\n"
        f"Account name: {payload['payment_account_name']}\n"
        f"Account number: {payload['payment_account_number']}\n"
        f"Paybill: {payload['payment_paybill']}\n"
        f"Due date: {invoice.due_date}\n"
    )

    csv_content = build_invoice_csv(invoice)
    pdf_content = build_invoice_pdf(invoice)
    subject = (
        f'SaccoSphere Monthly Platform Fee Invoice '
        f'({payload["period_start"]} to {payload["period_end"]})'
    )
    email = EmailMessage(
        subject=subject,
        body=message,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=list(recipients),
    )
    email.attach(
        filename=f'invoice-{invoice.id}.csv',
        content=csv_content,
        mimetype='text/csv',
    )
    email.attach(
        filename=f'invoice-{invoice.id}.pdf',
        content=pdf_content,
        mimetype='application/pdf',
    )
    email.send(fail_silently=False)

    invoice.status = MonthlySaccoInvoice.Status.SENT
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=['status', 'sent_at', 'updated_at'])
    return True


def build_invoice_csv(invoice):
    """Build CSV report payload for invoice download/email attachment."""
    output = StringIO()
    writer = csv.writer(output)
    payload = invoice.report_payload
    writer.writerow(['field', 'value'])
    writer.writerow(['sacco', invoice.sacco.name])
    writer.writerow(['period_start', payload.get('period_start')])
    writer.writerow(['period_end', payload.get('period_end')])
    writer.writerow(['transaction_count', payload.get('transaction_count')])
    writer.writerow(['total_transacted_amount', payload.get('total_transacted_amount')])
    writer.writerow(['fee_rate', payload.get('fee_rate')])
    writer.writerow(['amount_due', payload.get('amount_due')])
    writer.writerow(['payment_account_name', payload.get('payment_account_name')])
    writer.writerow(['payment_account_number', payload.get('payment_account_number')])
    writer.writerow(['payment_paybill', payload.get('payment_paybill')])
    writer.writerow(['due_date', str(invoice.due_date)])
    return output.getvalue()


def build_invoice_pdf(invoice):
    """Build simple PDF bytes report for invoice download/email attachment."""
    try:
        from weasyprint import HTML
    except Exception:
        # Fallback to plain-text bytes if PDF generator unavailable.
        return build_invoice_csv(invoice).encode('utf-8')

    payload = invoice.report_payload
    html = f"""
    <html>
    <body>
        <h1>SaccoSphere Monthly Invoice</h1>
        <p><strong>SACCO:</strong> {invoice.sacco.name}</p>
        <p><strong>Period:</strong> {payload.get('period_start')} to {payload.get('period_end')}</p>
        <p><strong>Transactions billed:</strong> {payload.get('transaction_count')}</p>
        <p><strong>Total transacted amount:</strong> KES {payload.get('total_transacted_amount')}</p>
        <p><strong>Fee rate:</strong> {payload.get('fee_rate')}</p>
        <p><strong>Amount due:</strong> KES {payload.get('amount_due')}</p>
        <h3>Payment Details</h3>
        <p><strong>Account name:</strong> {payload.get('payment_account_name')}</p>
        <p><strong>Account number:</strong> {payload.get('payment_account_number')}</p>
        <p><strong>Paybill:</strong> {payload.get('payment_paybill')}</p>
        <p><strong>Due date:</strong> {invoice.due_date}</p>
    </body>
    </html>
    """
    return HTML(string=html).write_pdf()
