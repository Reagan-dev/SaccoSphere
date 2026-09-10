"""
Automated compliance-flag detectors.

ComplianceFlag (see .models) previously had no automated writer anywhere in
the codebase - every flag had to be created by hand in Django admin. This
module is the detection layer: each detector inspects some operational
signal and, once a defined threshold is crossed, creates or updates a
ComplianceFlag for the affected SACCO. The existing dashboards
(superadmin_views.TopSaccosView, PlatformAlertsView) already read
ComplianceFlag, so a flag created here shows up there with no further UI
work.

HOW TO ADD A NEW DETECTOR
--------------------------
1. Subclass ComplianceDetector and set flag_type (a ComplianceFlag.FlagType
   value) and severity (a ComplianceFlag.Severity value).
2. Give it a method that inspects whatever signal it cares about and, once
   a threshold is crossed, calls `self.flag(sacco, description,
   metadata=...)`. Idempotency - updating an existing OPEN/INVESTIGATING
   flag of the same type+SACCO instead of creating a duplicate - is handled
   by the base class; individual detectors do not need to reimplement it.
3. Call the detector from wherever its signal naturally occurs (a callback
   handler, a periodic task, a serializer's validate(), ...). There is no
   automatic discovery/dispatch mechanism here, since each detector reacts
   to a fundamentally different kind of signal and lives closest to the
   code that produces it. Wrap the call in a try/except that logs rather
   than propagates, matching RepeatedPaymentFailureDetector's call site in
   payments/tasks.py - a compliance-flagging bug must never block the
   primary operation (payment processing, in that example) it's observing.

EXTENSION POINTS NOT YET IMPLEMENTED
-------------------------------------
Deliberately left as TODOs rather than guessed-at logic - these need real
product/regulatory sign-off on thresholds before they can be built:

- Liquidity breach detector: services.engines.liquidity_monitor already
  computes an "at risk" utilisation signal against
  SaccoSettings.liquidity_threshold_percentage, and already sends a
  LiquidityAlert notification when at risk. Turning that into a
  ComplianceFlag needs a decision on how many consecutive at-risk checks,
  or how far past threshold, actually warrants a platform-visible
  compliance flag versus the notification alone.
- KYC/IPRS failure-rate spikes: accounts.tasks.check_iprs_failure_rate
  already computes a failure rate periodically and could plausibly feed a
  detector here, but the rate/severity mapping is a compliance decision.
- SASRA regulatory reporting: a missed or late SASRA return deadline
  (saccomanagement.sasra_reports) is a natural REGULATORY-type flag, but
  needs a decision on the grace period before it's "missed."
- Repeated bulk SMS delivery failures for a SACCO (see
  saccomanagement.tasks.flag_stuck_sms_campaigns for a related but
  distinct existing safety net - that flags a *stuck* campaign, not a
  *pattern of delivery failure*).
"""

import logging
from datetime import timedelta

from django.utils import timezone

from .audit_logger import log_audit
from .models import ComplianceFlag


logger = logging.getLogger('saccosphere.compliance')


class ComplianceDetector:
    """
    Base class for a compliance detector.

    Subclasses set flag_type/severity and call self.flag(...) once their
    own signal crosses a threshold. This base class owns the shared
    idempotency behavior: one OPEN/INVESTIGATING ComplianceFlag per
    (sacco, flag_type) at a time, with repeat detections updating an
    occurrence count on the existing flag rather than creating duplicates.
    """

    flag_type = None
    severity = ComplianceFlag.Severity.MEDIUM

    def flag(self, sacco, description, metadata=None):
        if self.flag_type is None:
            raise NotImplementedError(
                f'{type(self).__name__} must set flag_type.',
            )

        metadata = dict(metadata) if metadata else {}
        now_iso = timezone.now().isoformat()

        existing = ComplianceFlag.objects.filter(
            sacco=sacco,
            flag_type=self.flag_type,
            status__in=[
                ComplianceFlag.Status.OPEN,
                ComplianceFlag.Status.INVESTIGATING,
            ],
        ).first()

        if existing is not None:
            existing_metadata = dict(existing.metadata or {})
            occurrence_count = existing_metadata.get('occurrence_count', 1) + 1
            existing_metadata.update(metadata)
            existing_metadata['occurrence_count'] = occurrence_count
            existing_metadata['last_seen_at'] = now_iso
            existing.metadata = existing_metadata
            existing.description = description
            existing.save(
                update_fields=['metadata', 'description', 'updated_at'],
            )
            logger.info(
                'Updated existing %s ComplianceFlag id=%s for sacco_id=%s '
                '(occurrence_count=%s).',
                self.flag_type,
                existing.id,
                sacco.id,
                occurrence_count,
            )
            log_audit(
                None,
                'UPDATE',
                'ComplianceFlag',
                existing.id,
                new_values={
                    'flag_type': self.flag_type,
                    'occurrence_count': occurrence_count,
                    'sacco_id': str(sacco.id),
                },
            )
            return existing, False

        metadata['occurrence_count'] = 1
        metadata['last_seen_at'] = now_iso
        created = ComplianceFlag.objects.create(
            sacco=sacco,
            flag_type=self.flag_type,
            severity=self.severity,
            description=description,
            metadata=metadata,
        )
        logger.warning(
            'Created new %s ComplianceFlag id=%s for sacco_id=%s.',
            self.flag_type,
            created.id,
            sacco.id,
        )
        log_audit(
            None,
            'CREATE',
            'ComplianceFlag',
            created.id,
            new_values={
                'flag_type': self.flag_type,
                'severity': self.severity,
                'sacco_id': str(sacco.id),
                'description': description,
            },
        )
        return created, True


# TODO(product): confirm real thresholds. 3 consecutive failures within a
# 1-hour rolling window is a placeholder for the reference implementation,
# not a compliance/business decision made here.
PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD = 3
PAYMENT_FAILURE_WINDOW_MINUTES = 60


class RepeatedPaymentFailureDetector(ComplianceDetector):
    """
    Flags a SACCO whose most recent M-Pesa/Daraja STK payment attempts have
    failed PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD times in a row within the
    last PAYMENT_FAILURE_WINDOW_MINUTES - a signal that the SACCO's Daraja
    configuration or the aggregator itself may be broken, not just an
    individual member's payment.
    """

    flag_type = ComplianceFlag.FlagType.PAYMENT_FAILURE
    severity = ComplianceFlag.Severity.HIGH

    def check(self, transaction):
        """
        Call this right after `transaction` has been marked FAILED by an
        M-Pesa STK callback. Returns the ComplianceFlag if the threshold
        was crossed, else None.
        """
        from payments.models import Transaction

        sacco = transaction.sacco
        if sacco is None:
            return None

        window_start = timezone.now() - timedelta(
            minutes=PAYMENT_FAILURE_WINDOW_MINUTES,
        )
        recent = list(
            Transaction.objects.filter(
                sacco=sacco,
                created_at__gte=window_start,
            ).order_by('-created_at')[:PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD]
        )

        if len(recent) < PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD:
            return None

        all_failed = all(
            txn.status == Transaction.Status.FAILED for txn in recent
        )
        if not all_failed:
            return None

        flag, _created = self.flag(
            sacco,
            description=(
                f'{PAYMENT_FAILURE_CONSECUTIVE_THRESHOLD} consecutive '
                'M-Pesa/Daraja payment failures for this SACCO within '
                f'{PAYMENT_FAILURE_WINDOW_MINUTES} minutes.'
            ),
            metadata={'latest_transaction_id': str(transaction.id)},
        )
        return flag


# The 90-day arrears line is SASRA's non-performing threshold - not a
# number chosen here. 30/60/90 staged NPLFlags + member/admin
# notifications remain the operational early-warning layer; only 90-day
# arrears escalate to a platform-visible compliance flag.
NPL_SEVERE_ARREARS_THRESHOLD_DAYS = 90


class SevereArrearsDetector(ComplianceDetector):
    """Platform-visible flag for a SACCO carrying non-performing loans.

    Why this exists alongside NPLFlag rather than replacing it: NPLFlag
    (services.engines.npl_monitor / services.tasks.flag_npl_arrears) is
    per-loan, staged at 30/60/90 days, and drives member + SACCO-admin
    notifications and the ACTIVE<->DEFAULTED transition - operational
    early warning owned by the SACCO. ComplianceFlag is the platform
    layer read by the superadmin dashboards (PlatformAlertsView,
    TopSaccosView). This detector bridges them: once a SACCO has one or
    more loans 90+ days in arrears, a single aggregate flag surfaces it
    platform-wide. It is refreshed (count/outstanding + occurrence_count
    + last_seen_at) on every daily sweep; a superadmin resolves it once
    the SACCO works the book back down - the same manual-resolution model
    as every other detector (see RepeatedPaymentFailureDetector,
    flag_stuck_sms_campaigns). A stale ``last_seen_at`` in the flag
    metadata is the signal that the arrears have since cleared.
    """

    flag_type = ComplianceFlag.FlagType.NPL
    severity = ComplianceFlag.Severity.HIGH

    def check(self, sacco, severe_loan_count, outstanding_balance):
        """Call once per SACCO that currently has 90+ day arrears.

        Returns the ComplianceFlag, or None when there is nothing to
        flag.
        """
        if not severe_loan_count or severe_loan_count <= 0:
            return None

        flag, _created = self.flag(
            sacco,
            description=(
                f'{severe_loan_count} loan(s) at this SACCO are '
                f'{NPL_SEVERE_ARREARS_THRESHOLD_DAYS}+ days in arrears '
                f'(KES {outstanding_balance:,.2f} outstanding) - '
                'non-performing under SASRA arrears rules.'
            ),
            metadata={
                'severe_loan_count': severe_loan_count,
                'outstanding_balance': str(outstanding_balance),
                'threshold_days': NPL_SEVERE_ARREARS_THRESHOLD_DAYS,
            },
        )
        return flag
