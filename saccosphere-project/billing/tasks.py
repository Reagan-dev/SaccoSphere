"""Billing automation tasks."""

import logging

from celery import shared_task
from django.core.mail import mail_admins

from accounts.models import Sacco
from billing.services import (
    generate_monthly_invoices_for_month,
    generate_monthly_sacco_invoice,
    previous_month_period,
    send_invoice_to_sacco,
)


logger = logging.getLogger('saccosphere.billing')


@shared_task(name='billing.tasks.generate_monthly_invoices')
def generate_monthly_invoices(billing_month=None):
    """Generate new-style monthly invoices from invoice line items."""
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
