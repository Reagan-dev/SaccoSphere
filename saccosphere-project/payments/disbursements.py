"""Shared M-Pesa B2C disbursement initiation helpers."""

from uuid import uuid4

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from config.utils import InvalidPhoneNumberError, normalize_phone_number

from .integrations.mpesa.daraja import (
    DarajaClient,
    DarajaError,
    format_phone_for_daraja,
)
from .models import MpesaTransaction, PaymentProvider, Transaction


def _get_b2c_callback_path():
    """Build B2C callback path with security token."""
    token = getattr(settings, 'MPESA_CALLBACK_TOKEN', '')
    if token:
        return f'/api/v1/payments/callback/mpesa/b2c/{token}/'
    return '/api/v1/payments/callback/mpesa/b2c/'


def _resolve_disbursement_phone_number(
    *,
    member_phone_number,
    requested_phone_number,
    override_reason,
):
    """Resolve and gate the phone number a B2C payout will be sent to.

    Defaults to ``member_phone_number`` - the member's own stored
    (OTP-verified) number, which the caller must source from the normal
    tenant-scoped ``loan.membership`` lookup, never from client input.

    A caller-supplied ``requested_phone_number`` that does not match it
    is only accepted when ``override_reason`` is a genuine, non-empty
    reason: the elevated-permission-gated alternate-number path
    (``B2CDisbursementAlternateNumberView``) is the only caller that
    should ever pass one. Any other caller sending a mismatched number
    without a reason is rejected - this is the fix for a SACCO admin (or
    anyone who compromises one) being able to silently redirect a payout.

    Returns ``(resolved_phone_number, is_alternate_number)`` - always
    E.164 (``+254...``), regardless of how ``member_phone_number`` happens
    to be stored - or ``(None, False)`` when the request must be
    rejected.
    """
    try:
        normalized_member_number = normalize_phone_number(
            member_phone_number,
        )
    except InvalidPhoneNumberError:
        return None, False

    if not requested_phone_number:
        return normalized_member_number, False

    try:
        normalized_requested_number = normalize_phone_number(
            requested_phone_number,
        )
    except InvalidPhoneNumberError:
        normalized_requested_number = None

    if normalized_requested_number == normalized_member_number:
        return normalized_member_number, False

    if normalized_requested_number and (override_reason or '').strip():
        return normalized_requested_number, True

    return None, False


def initiate_b2c_loan_disbursement(
    *,
    loan,
    remarks,
    phone_number=None,
    admin_user=None,
    request=None,
    override_reason=None,
):
    """
    Create a local B2C attempt, then initiate the outbound Daraja request.

    Includes fraud-aware fee calculation and audit logging.

    Disbursement amount
    --------------------
    There is no ``amount`` parameter: the disbursed amount is always
    ``loan.amount``, the approved principal - partial disbursement is
    not a supported feature. A caller-facing "amount" field (the normal
    disbursement request carries one, as a confirm-what-you're-about-to-
    disburse safety check) must be validated against ``loan.amount`` by
    the caller *before* reaching this function, and rejected on
    mismatch - not passed through here to be silently ignored.

    Payout phone number
    --------------------
    ``phone_number`` is normally omitted: the payout goes to the member's
    own stored (OTP-verified) ``phone_number``, resolved here from the
    same tenant-scoped ``loan.membership`` lookup everything else in this
    function already uses. Passing a *different* number requires
    ``override_reason`` to be a genuine, non-empty reason - callers
    without one who supply a mismatched number are rejected outright. The
    normal ``B2CDisbursementView`` never passes ``override_reason``; only
    the elevated-permission-gated (``IsSuperAdmin``)
    ``B2CDisbursementAlternateNumberView`` does, and it is responsible
    for enforcing that permission before this function is ever called.
    See ``_resolve_disbursement_phone_number``.

    Concurrency / idempotency contract
    ----------------------------------
    Validation and claiming the disbursement slot happen inside one
    ``db_transaction.atomic()`` block. Two independent guards make a second
    real Safaricom payout for the same loan impossible:

    1. ``select_for_update()`` on the loan row serialises concurrent callers
       on a backend with real row locking (PostgreSQL, used on Railway).
    2. The slot is then claimed with a single compare-and-swap UPDATE -
       ``... .filter(disbursement_status=PENDING).update(...)`` - and the
       affected-row count is checked. This is atomic on every backend,
       including ones where ``select_for_update()`` is a no-op, so a caller
       that loses the race (or a duplicate/retried request against an
       already-initiated disbursement) matches zero rows and is rejected
       with HTTP 409 *before* any Daraja call is made.

    The server-generated ``disbursement_idempotency_key`` stamped by the
    winning CAS is stored behind a ``unique`` constraint and echoed in the
    response, the ``Transaction`` metadata and the ``DisbursementAuditLog``
    row, so a retry can be correlated to the original attempt.

    The outbound Daraja HTTP call deliberately runs *outside* the
    transaction: holding a DB row lock across a slow network call is its
    own hazard.

    A genuine retry after a terminal ``FAILED`` (or a resolved
    ``PENDING_CONFIRMATION``) is only re-opened by the audited
    ``LoanAdmin.retry_failed_disbursement`` action, which resets
    ``disbursement_status`` to ``PENDING`` and clears the key.

    Returns (success: bool, payload: dict, http_status: int).
    """
    from payments.fee_calculator import SaccoInvoiceFeeCalculator
    from services.models import DisbursementAuditLog, Guarantor, Loan

    reference = f'SS-DSB-{uuid4().hex[:18].upper()}'

    with db_transaction.atomic():
        # Lock only the loan row (of=('self',)); the joined membership /
        # sacco / user rows are read-only here and locking them too would
        # widen the contention surface and invite deadlocks.
        loan = (
            Loan.objects.select_for_update(of=('self',))
            .select_related(
                'membership',
                'membership__sacco',
                'membership__user',
            )
            .get(id=loan.id)
        )
        sacco = loan.membership.sacco
        member = loan.membership.user

        # Duplicate/idempotency check FIRST: once a disbursement has been
        # claimed, loan.status itself moves off APPROVED, so this must be
        # tested before the APPROVED check or a retry would get a
        # misleading "only approved loans can be disbursed" 400.
        if loan.disbursement_status != loan.DisbursementStatus.PENDING:
            # Already claimed / initiated / completed - reject the duplicate
            # instead of firing a second Daraja call. 409, not 400: the
            # request is well-formed, it just lost the race for the slot.
            return False, {
                'error': (
                    'A disbursement for this loan is already in progress or '
                    'has completed. No second M-Pesa payout was initiated.'
                ),
                'disbursement_status': loan.disbursement_status,
                'idempotency_key': (
                    str(loan.disbursement_idempotency_key)
                    if loan.disbursement_idempotency_key
                    else None
                ),
                'conversation_id': loan.mpesa_conversation_id or None,
            }, 409

        # Validate loan status
        if loan.status != loan.Status.APPROVED:
            return False, {
                'error': 'Only approved loans can be disbursed.'
            }, 400

        if not member.phone_number:
            return False, {
                'error': 'Member phone number is required before disbursement.'
            }, 400

        target_phone_number, is_alternate_number = (
            _resolve_disbursement_phone_number(
                member_phone_number=member.phone_number,
                requested_phone_number=phone_number,
                override_reason=override_reason,
            )
        )
        if target_phone_number is None:
            return False, {
                'error': (
                    "Disbursement phone number does not match the "
                    "member's own registered number. Use the alternate-"
                    'number disbursement action to pay a different '
                    'number - it requires super admin authorization and '
                    'a reason.'
                ),
            }, 400

        # Validate guarantor approval
        pending_guarantors = loan.guarantors.filter(
            status=Guarantor.Status.PENDING
        ).exists()
        if pending_guarantors:
            return False, {
                'error': (
                    'Cannot disburse: loan has pending guarantor approvals. '
                    'All required guarantors must approve before disbursement.'
                )
            }, 400

        # Check if SACCO is payment-ready
        if not sacco.payment_ready:
            return False, {
                'error': 'This SACCO has not completed M-Pesa Daraja onboarding. '
                        'B2C disbursement is not yet available.'
            }, 400

        try:
            payment_config = sacco.payment_config
            if not payment_config.is_active:
                return False, {
                    'error': 'Payment configuration for this SACCO is not active.'
                }, 400
            if not payment_config.has_b2c_config():
                return False, {
                    'error': 'B2C disbursement not configured for this SACCO.'
                }, 400
        except AttributeError:
            return False, {
                'error': 'Payment configuration not found for this SACCO. '
                        'Please contact platform administration.'
            }, 400

        # Claim the disbursement slot with a compare-and-swap. The WHERE
        # clause pins the pre-state to PENDING, so exactly one of two
        # concurrent callers can match a row - the other gets 0 and is
        # rejected below. Atomic on every backend, not just those where
        # select_for_update() above actually locks.
        idempotency_key = uuid4()
        now = timezone.now()
        claimed_rows = Loan.objects.filter(
            pk=loan.pk,
            disbursement_status=loan.DisbursementStatus.PENDING,
        ).update(
            disbursement_status=loan.DisbursementStatus.INITIATING,
            disbursement_idempotency_key=idempotency_key,
            disbursement_initiated_at=now,
            updated_at=now,
        )
        if not claimed_rows:
            loan.refresh_from_db(
                fields=[
                    'disbursement_status',
                    'disbursement_idempotency_key',
                    'mpesa_conversation_id',
                ],
            )
            return False, {
                'error': (
                    'A disbursement for this loan is already in progress or '
                    'has completed. No second M-Pesa payout was initiated.'
                ),
                'disbursement_status': loan.disbursement_status,
                'idempotency_key': (
                    str(loan.disbursement_idempotency_key)
                    if loan.disbursement_idempotency_key
                    else None
                ),
                'conversation_id': loan.mpesa_conversation_id or None,
            }, 409
        loan.disbursement_status = loan.DisbursementStatus.INITIATING
        loan.disbursement_idempotency_key = idempotency_key
        loan.disbursement_initiated_at = now

        # Calculate fee breakdown
        calc = SaccoInvoiceFeeCalculator()
        breakdown = calc.calculate('disbursement', loan.amount)

        # Create payment provider record
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )

        # Create transaction with fee breakdown
        payment = Transaction.objects.create(
            provider=provider,
            sacco=sacco,
            user=member,
            reference=reference,
            transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
            amount=breakdown['net_amount'],
            gross_amount=breakdown['gross_amount'],
            platform_fee=breakdown['platform_fee'],
            fee_rate=None,
            status=Transaction.Status.PENDING,
            description=f'Loan disbursement - {loan.id}',
            metadata={
                'loan_id': str(loan.id),
                'idempotency_key': str(idempotency_key),
            },
        )

        mpesa_transaction = MpesaTransaction.objects.create(
            transaction=payment,
            phone_number=target_phone_number,
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan=loan,
        )

    # Initiate Daraja B2C. Deliberately outside the lock above: this is a
    # slow network call and must not hold a DB row lock while it runs.
    daraja_client = DarajaClient(
        consumer_key=payment_config.daraja_consumer_key,
        consumer_secret=payment_config.daraja_consumer_secret,
        shortcode=payment_config.shortcode,
        environment=payment_config.environment,
    )
    callback_url = daraja_client._build_callback_url(_get_b2c_callback_path())

    try:
        daraja_response = daraja_client.initiate_b2c(
            phone_number=format_phone_for_daraja(target_phone_number),
            amount=breakdown['net_amount'],
            occasion='Loan Disbursement',
            remarks=remarks,
            result_url=callback_url,
            timeout_url=callback_url,
            initiator_name=payment_config.b2c_initiator_name,
            security_credential=payment_config.b2c_security_credential,
        )
    except DarajaError as exc:
        is_timeout = exc.is_timeout
        _mark_b2c_attempt_failed(
            payment,
            mpesa_transaction,
            loan,
            exc,
            is_timeout=is_timeout,
        )
        if is_timeout:
            return False, {
                'error': (
                    'M-Pesa B2C initiation status is unknown. The attempt '
                    'was recorded and will be reconciled.'
                ),
                'transaction_id': str(payment.id),
                'idempotency_key': str(idempotency_key),
                'status': Loan.DisbursementStatus.PENDING_CONFIRMATION,
            }, 202
        return False, {
            'error': exc.message,
            'response_code': exc.response_code,
        }, 502

    conversation_id = daraja_response.get('ConversationID')
    originator_conversation_id = daraja_response.get(
        'OriginatorConversationID',
    )

    with db_transaction.atomic():
        payment.status = Transaction.Status.SENT
        payment.external_reference = conversation_id
        payment.metadata = {
            **payment.metadata,
            'daraja_response': daraja_response,
        }
        payment.save(
            update_fields=[
                'status',
                'external_reference',
                'metadata',
                'updated_at',
            ]
        )

        mpesa_transaction.conversation_id = conversation_id
        mpesa_transaction.originator_conversation_id = (
            originator_conversation_id
        )
        mpesa_transaction.save(
            update_fields=[
                'conversation_id',
                'originator_conversation_id',
                'updated_at',
            ]
        )

        # Update loan with disbursement details. disbursement_initiated_at
        # is left at the claim-time value set by the CAS above so
        # reconciliation ages the attempt from when it really started.
        loan.disbursement_transaction = payment
        loan.mpesa_transaction_record = mpesa_transaction
        loan.mpesa_conversation_id = conversation_id
        loan.disbursement_status = loan.DisbursementStatus.INITIATED
        loan.status = loan.Status.DISBURSEMENT_PENDING
        loan.save(
            update_fields=[
                'disbursement_transaction',
                'mpesa_transaction_record',
                'mpesa_conversation_id',
                'disbursement_status',
                'status',
                'updated_at',
            ],
        )

        # Exactly one audit row per initiation that actually reached
        # Safaricom. Written unconditionally (actor may be None for the
        # internal approval-flow caller) so the evidence trail never
        # depends on who triggered it.
        DisbursementAuditLog.objects.create(
            loan=loan,
            event='B2C_INITIATED',
            actor=admin_user,
            actor_role=(
                'super_admin' if is_alternate_number
                else 'sacco_admin' if admin_user
                else 'system'
            ),
            ip_address=_get_ip(request) or None,
            mpesa_ref=conversation_id or '',
            details={
                'conversation_id': conversation_id,
                'gross_amount': str(breakdown['gross_amount']),
                'platform_fee': str(breakdown['platform_fee']),
                'net_amount_sent': str(breakdown['net_amount']),
                'idempotency_key': str(idempotency_key),
                'alternate_number': is_alternate_number,
            },
        )

        # A second, distinctly-named row whenever this payout went to a
        # number other than the member's own - reason + approver
        # (actor/actor_role above already carry who and when) are the
        # evidence trail a compliance review needs without opening every
        # B2C_INITIATED row's details to find these.
        if is_alternate_number:
            DisbursementAuditLog.objects.create(
                loan=loan,
                event='B2C_ALTERNATE_NUMBER_AUTHORIZED',
                actor=admin_user,
                actor_role='super_admin',
                ip_address=_get_ip(request) or None,
                mpesa_ref=conversation_id or '',
                details={
                    'member_registered_number': member.phone_number,
                    'alternate_number': target_phone_number,
                    'reason': override_reason,
                    'approver_id': (
                        str(admin_user.id) if admin_user else None
                    ),
                    'approver_email': (
                        admin_user.email if admin_user else None
                    ),
                },
            )

    return True, {
        'status': loan.DisbursementStatus.INITIATED,
        'conversation_id': conversation_id,
        'idempotency_key': str(idempotency_key),
        'message': 'Disbursement initiated. Awaiting M-Pesa confirmation.',
    }, 201


def _get_ip(request) -> str:
    """Extract client IP from request, accounting for proxies."""
    if not request:
        return ''
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _mark_b2c_attempt_failed(
    payment,
    mpesa_transaction,
    loan,
    exc,
    *,
    is_timeout=False,
):
    """Record the outcome of a failed B2C initiation call.

    A timeout is genuinely ambiguous - Safaricom may have received and
    processed the request despite our client never seeing a response - so
    it is recorded as PENDING_CONFIRMATION, not FAILED, and left for
    reconcile_stale_mpesa_transactions to pick up rather than looking
    "safe to retry". A hard Daraja error (bad credentials, 4xx/5xx) is a
    genuine, unambiguous failure and keeps the existing FAILED handling.
    """
    from services.models import DisbursementAuditLog

    with db_transaction.atomic():
        payment.status = (
            Transaction.Status.INITIATION_FAILED
            if is_timeout
            else Transaction.Status.FAILED
        )
        payment.metadata = {
            **payment.metadata,
            'disbursement_error': exc.message,
            'daraja_error': {
                'message': exc.message,
                'response_code': exc.response_code,
                'status_unknown': is_timeout,
            },
        }
        payment.save(update_fields=['status', 'metadata', 'updated_at'])

        mpesa_transaction.result_code = exc.response_code
        mpesa_transaction.result_description = exc.message
        mpesa_transaction.save(
            update_fields=[
                'result_code',
                'result_description',
                'updated_at',
            ]
        )

        loan.disbursement_status = (
            loan.DisbursementStatus.PENDING_CONFIRMATION
            if is_timeout
            else loan.DisbursementStatus.FAILED
        )
        loan.save(update_fields=['disbursement_status', 'updated_at'])

        DisbursementAuditLog.objects.create(
            loan=loan,
            event='DISBURSEMENT_FAILED',
            actor=None,
            actor_role='system',
            details={
                'phase': 'initiation',
                'outcome': (
                    'timeout_ambiguous' if is_timeout else 'hard_failure'
                ),
                'message': exc.message,
                'response_code': exc.response_code,
                'idempotency_key': (
                    str(loan.disbursement_idempotency_key)
                    if loan.disbursement_idempotency_key
                    else None
                ),
            },
        )
