"""Celery tasks for SACCO services (loans, guarantors, savings)."""

import logging

from celery import shared_task
from django.db import transaction
from django.shortcuts import get_object_or_404

from notifications.tasks import notify_user_task

from .models import Guarantor, Loan


logger = logging.getLogger('saccosphere.services')


@shared_task(name='services.tasks.notify_guarantors')
def notify_guarantors_task(loan_id):
    """
    Notify all pending guarantors about a loan guarantee request.

    Retrieves the loan with related guarantors and sends notification
    to each guarantor with PENDING status via the notification system.
    Includes SMS notification for high-priority guarantor requests.

    Args:
        loan_id (str): UUID of the Loan object.

    Returns:
        int: Number of guarantors notified.

    Raises:
        Loan.DoesNotExist: If the loan is not found.
    """
    loan = get_object_or_404(Loan, id=loan_id)
    loan = loan.refresh_from_db() or loan

    pending_guarantors = Guarantor.objects.filter(
        loan=loan,
        status=Guarantor.Status.PENDING,
    ).select_related('guarantor')

    count = 0
    for guarantor in pending_guarantors:
        try:
            applicant_name = (
                f'{loan.membership.user.first_name} '
                f'{loan.membership.user.last_name}'
            )
            action_url = (
                f'/loans/{loan.id}/guarantors/{guarantor.id}/respond'
            )

            title = f'Guarantor Request — {applicant_name}'
            message = (
                f'You have been requested to guarantee a loan of '
                f'KES {loan.amount:.2f} for {applicant_name}. '
                f'Please respond in the SaccoSphere app.'
            )

            notify_user_task.delay(
                user_id=str(guarantor.guarantor.id),
                title=title,
                message=message,
                category='LOAN',
                action_url=action_url,
                send_sms=True,
                send_push=True,
                create_in_app=True,
            )

            count += 1
            logger.info(
                'Guarantor notification queued for guarantor_id=%s, loan_id=%s.',
                guarantor.id,
                loan.id,
            )

        except Exception as exc:
            logger.error(
                'Failed to queue guarantor notification for guarantor_id=%s: %s',
                guarantor.id,
                exc,
                exc_info=True,
            )
            continue

    return count


