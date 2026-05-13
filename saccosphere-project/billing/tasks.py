"""Billing automation tasks."""

import logging

from celery import shared_task

from accounts.models import Sacco
from billing.services import (
    generate_monthly_sacco_invoice,
    previous_month_period,
    send_invoice_to_sacco,
)


logger = logging.getLogger('saccosphere.billing')


@shared_task(name='billing.tasks.generate_and_send_monthly_fee_reports')
def generate_and_send_monthly_fee_reports():
    """Generate and email monthly SACCO platform fee invoices."""
    period_start, period_end = previous_month_period()
    processed = 0

    for sacco in Sacco.objects.filter(is_active=True):
        invoice = generate_monthly_sacco_invoice(
            sacco=sacco,
            period_start=period_start,
            period_end=period_end,
        )
        send_invoice_to_sacco(invoice)
        processed += 1

    logger.info(
        'Monthly platform fee reports processed for %s SACCOs.',
        processed,
    )
    return processed
