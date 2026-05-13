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


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# notify_guarantors_task is a Celery task that sends notifications
# to all guarantors when a loan application requires guarantees.
#
# Why async (Celery)?
# Sending notifications can be slow (SMS, push notifications, database
# writes). By using Celery, we don't block the HTTP request. The loan
# application returns to the user immediately while notifications are
# sent in the background.
#
# Key parameters:
# - loan_id: We pass the UUID as a string (JSON-serializable).
# - Celery automatically retries failed tasks (configured in config/celery.py).
#
# SMS notification:
# Guarantor requests are high-priority. A guarantor might miss an in-app
# notification, so we also send SMS (send_sms=True). This ensures the
# guarantor gets the request reliably.
#
# Returns count:
# Counts how many guarantor notifications were queued. If a guarantor's
# notification fails, we log it and continue (don't crash the task).
#
# One manual test:
# 1. Create a user "Alice" (guarantor).
# 2. Create a user "Bob" (applicant) with savings.
# 3. Call POST /api/v1/services/loans/apply/ as Bob for a SACCO loan
#    that requires guarantors (requires_guarantors=True).
# 4. In the apply response, set guarantors=[{"guarantor_id": Alice.id, ...}].
# 5. Call redis-cli and monitor the notifications queue:
#    redis-cli MONITOR
# 6. After ~30 seconds, check if Alice has a new Notification record:
#    SELECT * FROM notifications_notification WHERE user_id=Alice.id;
# 7. You should see the "Guarantor Request" notification.
#
# Important design decision:
# We don't validate guarantor capacity here. That's done in GuarantorRespondView
# when the guarantor approves. This allows guarantors to decline without
# triggering capacity checks, which is more user-friendly.
#
# END OF REVIEW - DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
