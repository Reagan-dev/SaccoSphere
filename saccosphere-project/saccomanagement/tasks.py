"""Background tasks for SACCO management workflows."""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from saccomanagement.import_utils import process_import_job  # noqa: F401


logger = logging.getLogger('saccosphere.saccomanagement')

# TODO(product): confirm a reasonable "stuck in SENDING" timeout.
STUCK_SMS_CAMPAIGN_TIMEOUT_HOURS = 1


@shared_task(name='saccomanagement.tasks.flag_stuck_sms_campaigns')
def flag_stuck_sms_campaigns():
    """
    Safety net for a worker crashing before send_bulk_sms_campaign_task
    ever reaches its own exception handler (so the retry-exhaustion status
    transition never runs): find campaigns that have sat in SENDING with
    no progress past a timeout and flag them for manual review via
    ComplianceFlag, rather than guessing their true final status.
    """
    from saccomanagement.models import ComplianceFlag, SMSCampaign

    cutoff = timezone.now() - timedelta(
        hours=STUCK_SMS_CAMPAIGN_TIMEOUT_HOURS,
    )
    stuck_campaigns = SMSCampaign.objects.filter(
        status=SMSCampaign.Status.SENDING,
        updated_at__lt=cutoff,
    ).select_related('sacco')

    flagged_count = 0
    for campaign in stuck_campaigns:
        already_flagged = ComplianceFlag.objects.filter(
            sacco=campaign.sacco,
            flag_type=ComplianceFlag.FlagType.PERFORMANCE,
            status__in=[
                ComplianceFlag.Status.OPEN,
                ComplianceFlag.Status.INVESTIGATING,
            ],
            metadata__campaign_id=str(campaign.id),
        ).exists()
        if already_flagged:
            continue

        ComplianceFlag.objects.create(
            sacco=campaign.sacco,
            flag_type=ComplianceFlag.FlagType.PERFORMANCE,
            severity=ComplianceFlag.Severity.MEDIUM,
            description=(
                f'SMS campaign {campaign.id} has been stuck in SENDING '
                f'for over {STUCK_SMS_CAMPAIGN_TIMEOUT_HOURS} hour(s) '
                'with no progress - the worker may have crashed before '
                'completing or retrying it. Needs manual review.'
            ),
            metadata={'campaign_id': str(campaign.id)},
        )
        flagged_count += 1
        logger.warning(
            'Flagged stuck SMS campaign_id=%s (sacco_id=%s) for manual '
            'review.',
            campaign.id,
            campaign.sacco_id,
        )

    return flagged_count
