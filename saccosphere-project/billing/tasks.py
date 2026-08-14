"""Billing automation tasks."""

import logging
from datetime import date, timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage, mail_admins
from django.utils import timezone

from accounts.models import Sacco
from billing.models import Invoice
from billing.services import (
    generate_monthly_invoices_for_month,
    generate_monthly_sacco_invoice,
    previous_month_period,
    send_invoice_to_sacco,
)


logger = logging.getLogger('saccosphere.billing')


@shared_task(name='billing.generate_monthly_invoices')
def generate_monthly_invoices():
    """
    Generate and send invoices for the previous month.

    Runs at 00:00 on the 1st of every month in the configured Django timezone,
    Africa/Nairobi.
    """
    from billing.invoice_generator import InvoiceGenerator
    from billing.pdf_generator import InvoicePDFGenerator

    today = timezone.localdate()
    first_of_current_month = today.replace(day=1)
    billing_month = (
        first_of_current_month - timedelta(days=1)
    ).replace(day=1)

    logger.info(
        'Starting monthly invoice generation for %s',
        billing_month,
    )

    generator = InvoiceGenerator()
    pdf_generator = InvoicePDFGenerator()

    for sacco in Sacco.objects.filter(is_active=True):
        try:
            invoice = generator.generate(sacco, billing_month)
            if invoice is None:
                logger.info(
                    'No transactions for SACCO %s -- skipping',
                    sacco.name,
                )
                continue

            pdf_bytes = pdf_generator.generate(invoice)
            pdf_generator.save(invoice, pdf_bytes)
            send_invoice_email.delay(str(invoice.id))

            logger.info(
                'Invoice %s generated for %s -- KES %s',
                invoice.invoice_number,
                sacco.name,
                invoice.total_amount,
            )
        except Exception as exc:
            logger.exception(
                'Failed to generate invoice for SACCO %s: %s',
                sacco.name,
                exc,
            )
            continue

    update_overdue_invoices.delay()


@shared_task(name='billing.send_invoice_email')
def send_invoice_email(invoice_id: str):
    """Send an invoice PDF to all SACCO admin email addresses."""
    from saccomanagement.models import Role

    invoice = Invoice.objects.select_related('sacco').get(id=invoice_id)
    admin_emails = list(
        Role.objects.filter(
            sacco=invoice.sacco,
            name=Role.SACCO_ADMIN,
        )
        .exclude(user__email='')
        .values_list('user__email', flat=True)
    )

    if not admin_emails:
        logger.warning(
            'No admin emails found for SACCO %s',
            invoice.sacco.name,
        )
        return

    subject = (
        f'SaccoSphere Invoice {invoice.invoice_number} -- '
        f'KES {invoice.total_amount:,.2f} due by {invoice.due_date}'
    )
    body = f"""
Dear {invoice.sacco.name} Admin,

Please find attached your SaccoSphere platform invoice for
{invoice.billing_month.strftime('%B %Y')}.

Invoice Number: {invoice.invoice_number}
Total Amount: KES {invoice.total_amount:,.2f}
Due Date: {invoice.due_date.strftime('%d %B %Y')}

Payment Instructions:
Paybill: {settings.BILLING_PAYBILL}
Account Number: {settings.BILLING_ACCOUNT_NUMBER}
Account Name: {settings.BILLING_ACCOUNT_NAME}

Note: Late payment attracts 2% interest per month.
Questions: billing@saccosphere.co.ke
"""
    email = EmailMessage(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        admin_emails,
    )

    with open(invoice.pdf_path, 'rb') as pdf:
        email.attach(
            f'{invoice.invoice_number}.pdf',
            pdf.read(),
            'application/pdf',
        )

    email.send()

    invoice.status = 'sent'
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=['status', 'sent_at'])


@shared_task(name='billing.update_overdue_invoices')
def update_overdue_invoices():
    """Mark invoices as overdue if past due date and unpaid."""
    overdue = Invoice.objects.filter(
        status='sent',
        due_date__lt=date.today(),
    )
    count = overdue.update(status='overdue')

    if count:
        logger.warning('%d invoices marked overdue', count)
        notify_superadmin.delay(
            f'{count} Overdue Invoices',
            'Check billing dashboard for overdue SACCO invoices.',
        )


@shared_task(name='billing.notify_superadmin')
def notify_superadmin(subject: str, message: str):
    """Notify platform admins about billing events."""
    mail_admins(subject=subject, message=message, fail_silently=True)


@shared_task(name='billing.tasks.generate_monthly_invoices')
def generate_monthly_invoices_for_current_month(billing_month=None):
    """Generate legacy new-style monthly invoices from invoice line items."""
    invoices = generate_monthly_invoices_for_month(billing_month)
    return [str(invoice.id) for invoice in invoices]


@shared_task(name='billing.tasks.generate_and_send_monthly_fee_reports')
def generate_and_send_monthly_fee_reports():
    """Generate and email monthly SACCO platform fee invoices."""
    period_start, period_end = previous_month_period()
    processed = 0
    failures = []

    for sacco in Sacco.objects.filter(is_active=True):
        try:
            invoice = generate_monthly_sacco_invoice(
                sacco=sacco,
                period_start=period_start,
                period_end=period_end,
            )
            send_invoice_to_sacco(invoice)
            processed += 1
        except Exception as exc:
            failure_entry = {
                'sacco_id': str(sacco.id),
                'sacco_name': sacco.name,
                'error': str(exc),
            }
            failures.append(failure_entry)
            logger.error(
                'Monthly invoice generation failed for SACCO %s: %s',
                sacco.name,
                exc,
                exc_info=True,
            )
            logger.exception(
                'Monthly platform fee report failed for sacco_id=%s.',
                sacco.id,
            )
            continue

    if failures:
        _notify_platform_admins_of_fee_report_failures(
            failures=failures,
            period_start=period_start,
            period_end=period_end,
        )

    logger.info(
        'Monthly platform fee reports processed for %s SACCOs; '
        '%s failures.',
        processed,
        len(failures),
    )
    return processed


def _notify_platform_admins_of_fee_report_failures(
    *,
    failures,
    period_start,
    period_end,
):
    failure_lines = [
        (
            f'- {failure["sacco_name"]} ({failure["sacco_id"]}): '
            f'{failure["error"]}'
        )
        for failure in failures
    ]
    message = (
        'Monthly platform fee report generation completed with failures.\n\n'
        f'Billing period: {period_start} to {period_end}\n'
        f'Failed SACCO count: {len(failures)}\n\n'
        + '\n'.join(failure_lines)
    )

    try:
        mail_admins(
            subject='SaccoSphere monthly fee report failures',
            message=message,
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            'Failed to notify platform admins about monthly fee failures.',
        )
