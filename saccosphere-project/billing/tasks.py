"""Billing automation tasks."""

import logging
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage, mail_admins
from django.db import DatabaseError, InterfaceError, OperationalError
from django.utils import timezone

from accounts.models import Sacco
from billing.models import Invoice
from billing.services import (
    generate_monthly_invoices_for_month,
    generate_monthly_sacco_invoice,
    previous_month_period,
    send_invoice_to_sacco,
)
from health.models import JobHeartbeat


logger = logging.getLogger('saccosphere.billing')

TRANSIENT_ERRORS = (DatabaseError, InterfaceError, OperationalError)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='billing.generate_monthly_invoices',
)
def generate_monthly_invoices(self):
    """
    Generate and send invoices for the previous month.

    Runs at 00:00 on the 1st of every month in the configured Django timezone,
    Africa/Nairobi. A per-SACCO failure is isolated and reported in the
    completion summary; a transient database error around the whole run
    retries the task with exponential backoff.
    """
    from billing.invoice_generator import InvoiceGenerator
    from billing.pdf_generator import InvoicePDFGenerator
    from config.utils import emit_metric

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
    generated_count = 0
    failures = []

    try:
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
                generated_count += 1
                emit_metric(
                    'billing_invoice_generated',
                    sacco_id=str(sacco.id),
                    invoice_number=invoice.invoice_number,
                    amount=str(invoice.total_amount),
                )

                logger.info(
                    'Invoice %s generated for %s -- KES %s',
                    invoice.invoice_number,
                    sacco.name,
                    invoice.total_amount,
                )
            except Exception as exc:
                failures.append({
                    'sacco_id': str(sacco.id),
                    'sacco_name': sacco.name,
                    'error': str(exc),
                })
                logger.exception(
                    'Failed to generate invoice for SACCO %s: %s',
                    sacco.name,
                    exc,
                )
                continue

        if failures:
            _notify_platform_admins_of_fee_report_failures(
                failures=failures,
                period_start=billing_month,
                period_end=billing_month,
            )

        JobHeartbeat.record(
            'generate_monthly_invoices',
            status=(
                JobHeartbeat.Status.ERROR if failures
                else JobHeartbeat.Status.OK
            ),
            detail={
                'billing_month': str(billing_month),
                'invoices_generated': generated_count,
                'failures': len(failures),
            },
        )

        update_overdue_invoices.delay()
    except TRANSIENT_ERRORS as exc:
        if self.request.retries >= self.max_retries:
            logger.exception(
                'Monthly invoice generation exhausted transient retries '
                'for billing_month=%s.',
                billing_month,
            )
            JobHeartbeat.record(
                'generate_monthly_invoices',
                status=JobHeartbeat.Status.ERROR,
                detail={'billing_month': str(billing_month), 'error': str(exc)},
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Monthly invoice generation hit a transient error. '
            'Retrying in %s seconds.',
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='billing.send_invoice_email',
)
def send_invoice_email(self, invoice_id: str):
    """Send an invoice PDF to all SACCO admin email addresses."""
    from saccomanagement.models import Role

    invoice = Invoice.objects.select_related('sacco').get(id=invoice_id)
    admin_emails = list(
        Role.objects.filter(
            sacco=invoice.sacco,
            name=Role.SACCO_ADMIN,
            is_active=True,
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

    if not invoice.pdf_path or not Path(invoice.pdf_path).exists():
        logger.error(
            'Invoice PDF missing for invoice_id=%s (sacco=%s, path=%r); '
            'skipping send.',
            invoice.id,
            invoice.sacco.name,
            invoice.pdf_path,
        )
        notify_superadmin.delay(
            f'Invoice {invoice.invoice_number} PDF missing',
            (
                f'Could not send invoice {invoice.invoice_number} for '
                f'{invoice.sacco.name}: PDF file not found at '
                f'{invoice.pdf_path!r}. Regenerate and resend manually.'
            ),
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

Questions: billing@saccosphere.co.ke
"""
    email = EmailMessage(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        admin_emails,
    )

    try:
        with open(invoice.pdf_path, 'rb') as pdf:
            email.attach(
                f'{invoice.invoice_number}.pdf',
                pdf.read(),
                'application/pdf',
            )

        email.send()
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Invoice email failed for invoice_id=%s. Retrying in %s '
            'seconds.',
            invoice.id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)

    invoice.status = 'sent'
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=['status', 'sent_at'])

    from config.utils import emit_metric

    emit_metric(
        'billing_invoice_sent',
        sacco_id=str(invoice.sacco_id),
        invoice_number=invoice.invoice_number,
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='billing.update_overdue_invoices',
)
def update_overdue_invoices(self):
    """Mark invoices as overdue if past due date and unpaid."""
    from config.utils import emit_metric

    try:
        overdue = Invoice.objects.filter(
            status='sent',
            due_date__lt=timezone.localdate(),
        )
        count = overdue.update(status='overdue')

        if count:
            logger.warning('%d invoices marked overdue', count)
            emit_metric('billing_invoices_marked_overdue', count=count)
            notify_superadmin.delay(
                f'{count} Overdue Invoices',
                'Check billing dashboard for overdue SACCO invoices.',
            )

        JobHeartbeat.record(
            'update_overdue_invoices',
            detail={'invoices_marked_overdue': count},
        )
    except TRANSIENT_ERRORS as exc:
        if self.request.retries >= self.max_retries:
            logger.exception(
                'Overdue-invoice sweep exhausted transient retries.',
            )
            JobHeartbeat.record(
                'update_overdue_invoices',
                status=JobHeartbeat.Status.ERROR,
                detail={'error': str(exc)},
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Overdue-invoice sweep hit a transient error. Retrying in '
            '%s seconds.',
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(name='billing.notify_superadmin')
def notify_superadmin(subject: str, message: str):
    """Notify platform admins about billing events."""
    mail_admins(subject=subject, message=message, fail_silently=True)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='billing.suspend_overdue_saccos',
)
def suspend_overdue_saccos(self):
    """Suspend SACCO admin writes for invoices overdue beyond grace period."""
    from config.utils import emit_metric
    from saccomanagement.audit_logger import log_audit

    try:
        today = timezone.localdate()
        cutoff = today - timedelta(days=8)
        overdue_invoices = Invoice.objects.filter(
            status='overdue',
            due_date__lte=cutoff,
        ).select_related('sacco')

        suspended_count = 0
        for invoice in overdue_invoices:
            sacco = invoice.sacco
            if sacco.is_billing_suspended:
                continue

            sacco.is_billing_suspended = True
            sacco.suspended_at = timezone.now()
            sacco.suspension_reason = (
                f'Invoice {invoice.invoice_number} overdue by '
                f'{(today - invoice.due_date).days} days'
            )
            sacco.save(
                update_fields=[
                    'is_billing_suspended',
                    'suspended_at',
                    'suspension_reason',
                    'updated_at',
                ],
            )

            invoice.status = 'suspended'
            invoice.save(update_fields=['status', 'updated_at'])
            send_suspension_notice.delay(str(sacco.id), str(invoice.id))
            emit_metric(
                'billing_sacco_suspended',
                sacco_id=str(sacco.id),
                invoice_number=invoice.invoice_number,
            )
            log_audit(
                None,
                'SACCO_BILLING_SUSPENDED',
                'Sacco',
                sacco.id,
                new_values={
                    'invoice_id': str(invoice.id),
                    'invoice_number': invoice.invoice_number,
                    'suspension_reason': sacco.suspension_reason,
                },
            )
            suspended_count += 1

        if suspended_count:
            logger.warning(
                '%d SACCOs suspended for overdue billing',
                suspended_count,
            )

        JobHeartbeat.record(
            'suspend_overdue_saccos',
            detail={'saccos_suspended': suspended_count},
        )

        return suspended_count
    except TRANSIENT_ERRORS as exc:
        if self.request.retries >= self.max_retries:
            logger.exception(
                'Billing suspension sweep exhausted transient retries.',
            )
            JobHeartbeat.record(
                'suspend_overdue_saccos',
                status=JobHeartbeat.Status.ERROR,
                detail={'error': str(exc)},
            )
            raise

        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Billing suspension sweep hit a transient error. Retrying '
            'in %s seconds.',
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='billing.send_suspension_notice',
)
def send_suspension_notice(self, sacco_id: str, invoice_id: str):
    """Notify SACCO admins that billing suspension has been applied."""
    sacco = Sacco.objects.get(id=sacco_id)
    invoice = Invoice.objects.get(id=invoice_id)
    admin_emails = _get_sacco_admin_emails(sacco)

    if not admin_emails:
        logger.warning('No admin emails found for SACCO %s', sacco.name)
        return

    subject = f'SaccoSphere access suspended for {sacco.name}'
    body = (
        f'Dear {sacco.name} Admin,\n\n'
        'Your SACCO admin portal write access has been suspended because '
        f'invoice {invoice.invoice_number} remains unpaid after its due '
        f'date of {invoice.due_date:%d %B %Y}.\n\n'
        'Please pay the outstanding invoice to restore full admin access.\n'
    )
    try:
        EmailMessage(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            admin_emails,
        ).send()
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Suspension notice failed for sacco_id=%s. Retrying in %s '
            'seconds.',
            sacco_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='billing.send_payment_received_notice',
)
def send_payment_received_notice(self, sacco_id: str, invoice_id: str):
    """Notify SACCO admins that payment was received and access restored."""
    sacco = Sacco.objects.get(id=sacco_id)
    invoice = Invoice.objects.get(id=invoice_id)
    admin_emails = _get_sacco_admin_emails(sacco)

    if not admin_emails:
        logger.warning('No admin emails found for SACCO %s', sacco.name)
        return

    subject = f'Payment received for invoice {invoice.invoice_number}'
    body = (
        f'Dear {sacco.name} Admin,\n\n'
        'Payment received. Account restored.\n\n'
        f'Invoice {invoice.invoice_number} has been marked paid, and your '
        'SACCO admin portal access has been restored.\n\n'
        'Thank you for using SaccoSphere.\n'
    )
    try:
        EmailMessage(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            admin_emails,
        ).send()
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Payment-received notice failed for sacco_id=%s. Retrying '
            'in %s seconds.',
            sacco_id,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)


def _get_sacco_admin_emails(sacco):
    from saccomanagement.models import Role

    return list(
        Role.objects.filter(
            sacco=sacco,
            name=Role.SACCO_ADMIN,
            is_active=True,
        )
        .exclude(user__email='')
        .values_list('user__email', flat=True)
    )


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
