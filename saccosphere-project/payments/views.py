import json
import logging
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from amqp.exceptions import ConnectionError as AmqpConnectionError
from django.core.cache import cache
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from requests.exceptions import Timeout as RequestsTimeout
from rest_framework import serializers, status
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin
from config.response import StandardResponseMixin
from guarantor.utils import check_loan_guarantors_complete
from payments.disbursements import initiate_b2c_loan_disbursement
from payments.providers import get_psp_provider
from payments.providers.registry import get_provider_class
from services.models import Loan, Saving

from .fee_calculator import SaccoInvoiceFeeCalculator
from .integrations.mpesa.daraja import DarajaClient, DarajaError
from .integrations.mpesa.security import (
    is_replay_attack,
    is_safaricom_ip,
    verify_mpesa_signature,
)
from .models import Callback, MpesaTransaction, PaymentProvider, Transaction
from .serializers import (
    CallbackSerializer,
    DepositRequestSerializer,
    MpesaTransactionSerializer,
    TransactionSerializer,
    WithdrawalRequestSerializer,
)
from .tasks import process_payment_callback
from .validators import validate_mpesa_phone


logger = logging.getLogger('saccosphere.payments')


BROKER_CONNECTION_ERRORS = (
    AmqpConnectionError,
    KombuOperationalError,
    RedisConnectionError,
    RedisTimeoutError,
)


def _get_mpesa_provider_record():
    provider, _ = PaymentProvider.objects.get_or_create(
        name='M-Pesa',
        defaults={
            'provider_type': PaymentProvider.ProviderType.MPESA,
            'is_active': True,
        },
    )
    return provider


def _persist_mpesa_enqueue_failure(
    *,
    callback_body,
    error,
    callback_type,
    mpesa_transaction=None,
):
    transaction = None
    if mpesa_transaction is not None:
        transaction = mpesa_transaction.transaction

    return Callback.objects.create(
        transaction=transaction,
        provider=_get_mpesa_provider_record(),
        raw_payload={
            'callback_type': callback_type,
            'payload': callback_body,
        },
        processed=False,
        processing_error=str(error),
    )


def _retry_mpesa_response(result_desc='Temporary processing unavailable'):
    return JsonResponse(
        {'ResultCode': 1, 'ResultDesc': result_desc},
        status=503,
    )


def _get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _clear_mpesa_replay_marker(callback_identifier):
    try:
        cache.delete(f'mpesa_replay:{callback_identifier}')
    except Exception:
        logger.warning(
            'Failed to clear M-Pesa replay marker for %s.',
            callback_identifier,
            exc_info=True,
        )


class STKPushRequestSerializer(serializers.Serializer):
    SAVING_DEPOSIT = 'SAVING_DEPOSIT'
    LOAN_REPAYMENT = 'LOAN_REPAYMENT'

    PURPOSE_CHOICES = (
        (SAVING_DEPOSIT, 'Saving deposit'),
        (LOAN_REPAYMENT, 'Loan repayment'),
    )

    phone_number = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    purpose = serializers.ChoiceField(choices=PURPOSE_CHOICES)
    sacco_id = serializers.UUIDField()
    saving_id = serializers.UUIDField(required=False)
    loan_id = serializers.UUIDField(required=False)
    instalment_number = serializers.IntegerField(
        min_value=1,
        required=False,
    )

    def validate_phone_number(self, value):
        return validate_mpesa_phone(value)

    def validate_amount(self, value):
        if value < Decimal('10.00'):
            raise serializers.ValidationError('Amount must be at least 10.')

        if value > Decimal('300000.00'):
            raise serializers.ValidationError(
                'Amount cannot be more than 300000.'
            )

        return value

    def validate(self, attrs):
        purpose = attrs['purpose']

        if purpose == self.SAVING_DEPOSIT:
            if not attrs.get('saving_id'):
                raise serializers.ValidationError(
                    {
                        'saving_id': (
                            'This field is required for saving deposits.'
                        ),
                    }
                )

            return attrs

        if not attrs.get('loan_id'):
            raise serializers.ValidationError(
                {'loan_id': 'This field is required for loan repayments.'}
            )

        if not attrs.get('instalment_number'):
            raise serializers.ValidationError(
                {
                    'instalment_number': (
                        'This field is required for loan repayments.'
                    ),
                }
            )

        return attrs


class B2CDisbursementSerializer(serializers.Serializer):
    loan_id = serializers.UUIDField()
    phone_number = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    remarks = serializers.CharField(
        default='Loan Disbursement',
        required=False,
    )

    def validate_phone_number(self, value):
        return validate_mpesa_phone(value)

    def validate_amount(self, value):
        if value <= Decimal('0.00'):
            raise serializers.ValidationError(
                'Amount must be greater than zero.'
            )

        return value


class DepositInitiateView(APIView):
    """Initiate a PSP-backed deposit for the authenticated user."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DepositRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sacco = data['sacco']
        if not serializer.validate_membership(request.user):
            return Response(
                {
                    'detail': (
                        'You must have an approved membership in this SACCO '
                        'before making a deposit.'
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        provider = get_psp_provider(sacco=sacco)
        provider_record, _ = PaymentProvider.objects.get_or_create(
            name=provider.provider_name,
            defaults={
                'provider_type': PaymentProvider.ProviderType.INTERNAL,
                'is_active': True,
            },
        )

        transaction = Transaction(
            provider=provider_record,
            user=request.user,
            reference=f'SS-{uuid4().hex[:20].upper()}',
            transaction_type=Transaction.TransactionType.DEPOSIT,
            amount=data['net_amount'],
            gross_amount=data['gross_amount'],
            platform_fee=data['platform_fee'],
            fee_rate=data['fee_rate'],
            sacco=sacco,
            fee_amount=data['platform_fee'],
            currency='KES',
            status=Transaction.Status.PENDING,
            description='Deposit initiated',
            metadata={
                'sacco_id': str(sacco.id),
                'amount': str(data['net_amount']),
                'net_amount': str(data['net_amount']),
                'platform_fee': str(data['platform_fee']),
                'gross_amount': str(data['gross_amount']),
                'fee_rate': str(data['fee_rate']),
            },
        )

        try:
            with db_transaction.atomic():
                transaction.save()
                result = provider.create_checkout(
                    transaction_id=str(transaction.id),
                    phone=data['phone_number'],
                    gross_amount=data['gross_amount'],
                    sacco=sacco,
                    net_amount=data['net_amount'],
                    platform_fee=data['platform_fee'],
                )
                transaction.external_reference = result.provider_reference
                transaction.save(
                    update_fields=['external_reference', 'updated_at'],
                )
        except Exception:
            logger.exception(
                'Deposit initiation failed for transaction %s',
                transaction.id,
            )
            try:
                with db_transaction.atomic():
                    transaction.status = Transaction.Status.FAILED
                    transaction.save(update_fields=['status', 'updated_at'])
            except Exception:
                logger.exception(
                    'Failed to update transaction %s to FAILED',
                    transaction.id,
                )
            return Response(
                {'detail': 'Deposit initiation failed.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Format breakdown for frontend confirmation
        def fmt(v):
            return f"KES {v:,.2f}"

        return Response(
            {
                'transaction_id': str(transaction.id),
                'amount_depositing': fmt(data['net_amount']),
                'platform_fee': fmt(data['platform_fee']),
                'total_charged': fmt(data['gross_amount']),
                'savings_credited': fmt(data['net_amount']),
                'status': transaction.status,
            },
            status=status.HTTP_200_OK,
        )


class WithdrawalInitiateView(APIView):
    """Initiate a PSP-backed savings withdrawal for the authenticated user."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WithdrawalRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        is_valid, detail = serializer.validate_withdrawal_context(
            request.user,
        )
        if not is_valid:
            return Response(
                {'detail': detail},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        sacco = data['sacco']
        saving = data['saving']
        provider = get_psp_provider(sacco=sacco)
        provider_record, _ = PaymentProvider.objects.get_or_create(
            name=provider.provider_name,
            defaults={
                'provider_type': PaymentProvider.ProviderType.INTERNAL,
                'is_active': True,
            },
        )

        transaction = Transaction(
            provider=provider_record,
            user=request.user,
            reference=f'SS-WD-{uuid4().hex[:18].upper()}',
            transaction_type=Transaction.TransactionType.WITHDRAWAL,
            amount=data['net_amount'],
            gross_amount=data['gross_amount'],
            platform_fee=data['platform_fee'],
            fee_rate=data['fee_rate'],
            sacco=sacco,
            fee_amount=data['platform_fee'],
            currency='KES',
            status=Transaction.Status.PENDING,
            description='Savings withdrawal initiated',
            metadata={
                'sacco_id': str(sacco.id),
                'saving_id': str(saving.id),
                'amount': str(data['net_amount']),
                'net_amount': str(data['net_amount']),
                'platform_fee': str(data['platform_fee']),
                'gross_amount': str(data['gross_amount']),
                'fee_rate': str(data['fee_rate']),
            },
        )

        try:
            with db_transaction.atomic():
                transaction.save()
                result = provider.disburse(
                    transaction_id=str(transaction.id),
                    phone=data['phone_number'],
                    amount=data['net_amount'],
                    reference=f'WD-{transaction.id}',
                    sacco=sacco,
                    saving=saving,
                )
                if not result.success:
                    transaction.status = Transaction.Status.FAILED
                    transaction.metadata = {
                        **transaction.metadata,
                        'provider_error': result.error_message,
                        'provider_response': result.raw_response,
                    }
                    transaction.save(
                        update_fields=[
                            'status',
                            'metadata',
                            'updated_at',
                        ],
                    )
                    return Response(
                        {'detail': 'Withdrawal initiation failed.'},
                        status=status.HTTP_502_BAD_GATEWAY,
                    )

                transaction.status = Transaction.Status.SENT
                transaction.external_reference = result.provider_reference
                transaction.metadata = {
                    **transaction.metadata,
                    'provider_response': result.raw_response,
                }
                transaction.save(
                    update_fields=[
                        'status',
                        'external_reference',
                        'metadata',
                        'updated_at',
                    ],
                )
        except Exception:
            logger.exception(
                'Withdrawal initiation failed for transaction %s',
                transaction.id,
            )
            try:
                with db_transaction.atomic():
                    transaction.status = Transaction.Status.FAILED
                    transaction.save(update_fields=['status', 'updated_at'])
            except Exception:
                logger.exception(
                    'Failed to update transaction %s to FAILED',
                    transaction.id,
                )
            return Response(
                {'detail': 'Withdrawal initiation failed.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        def fmt(value):
            return f"KES {value:,.2f}"

        return Response(
            {
                'transaction_id': str(transaction.id),
                'amount_requested': fmt(data['gross_amount']),
                'platform_fee': fmt(data['platform_fee']),
                'amount_to_member': fmt(data['net_amount']),
                'status': transaction.status,
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentCallbackView(APIView):
    """Receive a PSP callback and queue asynchronous processing."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @method_decorator(csrf_exempt, name='dispatch')
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        provider = get_psp_provider()
        provider_name = provider.provider_name

        try:
            is_valid = provider.verify_webhook(request)
        except Exception as exc:
            logger.warning(
                'Payment callback verification raised an exception for provider %s: %s',
                provider_name,
                exc,
            )
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        if not is_valid:
            logger.warning('Payment callback rejected by provider %s', provider_name)
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if hasattr(request, 'data') else {}
        payload_preview = str(payload)[:500]
        logger.info(
            'Payment callback received from provider %s with payload %s',
            provider_name,
            payload_preview,
        )

        provider_record, _ = PaymentProvider.objects.get_or_create(
            name=provider_name,
            defaults={
                'provider_type': PaymentProvider.ProviderType.INTERNAL,
                'is_active': True,
            },
        )
        callback = Callback.objects.create(
            raw_payload=payload,
            provider=provider_record,
            processed=False,
        )

        try:
            process_payment_callback.delay(str(callback.id))
        except Exception as exc:
            logger.exception('Failed to enqueue callback processing for %s', callback.id)
            callback.processing_error = str(exc)
            callback.save(update_fields=['processing_error'])

        return Response({'received': True}, status=status.HTTP_200_OK)


class TransactionListView(StandardResponseMixin, ListAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Transaction.objects.select_related('provider').filter(
            user=self.request.user,
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return self.ok(serializer.data)


class TransactionDetailView(StandardResponseMixin, RetrieveAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Transaction.objects.select_related('provider').filter(
            user=self.request.user,
        )

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return self.ok(serializer.data)


class MpesaTransactionDetailView(StandardResponseMixin, RetrieveAPIView):
    serializer_class = MpesaTransactionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return MpesaTransaction.objects.select_related(
            'transaction',
        ).filter(transaction__user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return self.ok(serializer.data)


class STKPushView(APIView):
    IDEMPOTENCY_WINDOW = timedelta(minutes=2)
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Initiate M-Pesa STK push for saving or loan repayment.',
        request_body=STKPushRequestSerializer,
        responses={201: openapi.Response('Created'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def post(self, request):
        serializer = STKPushRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        related_saving = None
        related_loan = None

        if data['purpose'] == STKPushRequestSerializer.SAVING_DEPOSIT:
            related_saving = self._get_owned_saving(request.user, data)
            transaction_type = Transaction.TransactionType.DEPOSIT
            description = 'SaccoSphere saving deposit'
        else:
            related_loan = self._get_owned_loan(request.user, data)
            transaction_type = Transaction.TransactionType.LOAN_REPAYMENT
            description = 'SaccoSphere loan repayment'

        net_amount = data['amount']
        business_type = (
            'deposit'
            if transaction_type == Transaction.TransactionType.DEPOSIT
            else 'repayment'
        )
        fee_breakdown = SaccoInvoiceFeeCalculator().calculate(
            business_type,
            net_amount,
        )
        sacco = (
            related_saving.membership.sacco
            if related_saving
            else related_loan.membership.sacco
        )

        existing_mpesa = self._get_existing_stk_attempt(
            request.user,
            data,
            fee_breakdown,
            transaction_type,
            related_saving,
            related_loan,
        )
        if existing_mpesa is not None:
            return self._existing_stk_response(
                existing_mpesa,
                fee_breakdown,
                transaction_type,
            )

        reference = self._build_reference()
        payment, mpesa_transaction = self._create_local_stk_attempt(
            user=request.user,
            data=data,
            transaction_type=transaction_type,
            description=description,
            reference=reference,
            fee_breakdown=fee_breakdown,
            sacco=sacco,
            related_saving=related_saving,
            related_loan=related_loan,
        )

        try:
            daraja_response = DarajaClient().initiate_stk_push(
                phone_number=data['phone_number'],
                amount=fee_breakdown['gross_amount'],
                account_reference=reference,
                description=description,
                callback_path='/api/v1/payments/callback/mpesa/stk/',
            )
        except DarajaError as exc:
            is_timeout = isinstance(exc.__cause__, RequestsTimeout)
            self._mark_stk_initiation_failed(
                payment,
                mpesa_transaction,
                exc,
                status_unknown=is_timeout,
            )
            payment.refresh_from_db()
            logger.error(
                'M-Pesa STK push failed for user %s: %s (code=%s)',
                request.user.email,
                exc.message,
                exc.response_code,
                exc_info=True,
            )
            if is_timeout:
                return Response(
                    {
                        'detail': (
                            'M-Pesa STK initiation status is unknown. '
                            'The attempt was recorded and will be reconciled.'
                        ),
                        'transaction_id': str(payment.id),
                        'status': payment.status,
                    },
                    status=status.HTTP_202_ACCEPTED,
                )

            return Response(
                {
                    'error': exc.message,
                    'response_code': exc.response_code,
                    'transaction_id': str(payment.id),
                    'status': payment.status,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        merchant_request_id = daraja_response.get('MerchantRequestID')
        checkout_request_id = daraja_response.get('CheckoutRequestID')
        if not merchant_request_id or not checkout_request_id:
            exc = DarajaError(
                'M-Pesa STK response did not include checkout identifiers.',
                daraja_response.get('ResponseCode'),
            )
            self._mark_stk_initiation_failed(
                payment,
                mpesa_transaction,
                exc,
                daraja_response=daraja_response,
            )
            payment.refresh_from_db()
            return Response(
                {
                    'error': exc.message,
                    'response_code': exc.response_code,
                    'transaction_id': str(payment.id),
                    'status': payment.status,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        self._mark_stk_initiation_accepted(
            payment,
            mpesa_transaction,
            daraja_response,
            merchant_request_id,
            checkout_request_id,
        )

        # Build clear message showing total charge breakdown
        amount_description = self._amount_description(transaction_type)

        message = (
            f'Check your phone to enter your M-Pesa PIN. '
            f'You will be charged KES {fee_breakdown["gross_amount"]} '
            f'for {amount_description}.'
        )

        return Response(
            {
                'checkout_request_id': checkout_request_id,
                'merchant_request_id': merchant_request_id,
                'amount': str(fee_breakdown['net_amount']),
                'gross_amount': str(fee_breakdown['gross_amount']),
                'platform_fee': str(fee_breakdown['platform_fee']),
                'message': message,
            },
            status=status.HTTP_201_CREATED,
        )

    def _get_existing_stk_attempt(
        self,
        user,
        data,
        fee_breakdown,
        transaction_type,
        related_saving,
        related_loan,
    ):
        cutoff = timezone.now() - self.IDEMPOTENCY_WINDOW
        terminal_statuses = [
            Transaction.Status.COMPLETED,
            Transaction.Status.FAILED,
            Transaction.Status.AMOUNT_MISMATCH,
            Transaction.Status.REVERSED,
        ]
        queryset = MpesaTransaction.objects.select_related(
            'transaction',
        ).filter(
            transaction__user=user,
            transaction__transaction_type=transaction_type,
            transaction__amount=fee_breakdown['net_amount'],
            transaction__created_at__gte=cutoff,
            phone_number=data['phone_number'],
        ).exclude(transaction__status__in=terminal_statuses)

        if related_saving is not None:
            queryset = queryset.filter(related_saving=related_saving)
        else:
            queryset = queryset.filter(
                related_loan=related_loan,
                related_instalment_number=data.get('instalment_number'),
            )

        return queryset.order_by('-created_at').first()

    def _existing_stk_response(
        self,
        mpesa_transaction,
        fee_breakdown,
        transaction_type,
    ):
        payment = mpesa_transaction.transaction
        amount_description = self._amount_description(transaction_type)
        message = (
            f'An M-Pesa prompt is already active for this '
            f'{amount_description}.'
        )
        response_status = (
            status.HTTP_200_OK
            if mpesa_transaction.checkout_request_id
            else status.HTTP_202_ACCEPTED
        )

        return Response(
            {
                'transaction_id': str(payment.id),
                'checkout_request_id': mpesa_transaction.checkout_request_id,
                'merchant_request_id': mpesa_transaction.merchant_request_id,
                'amount': str(fee_breakdown['net_amount']),
                'gross_amount': str(fee_breakdown['gross_amount']),
                'platform_fee': str(fee_breakdown['platform_fee']),
                'status': payment.status,
                'duplicate': True,
                'message': message,
            },
            status=response_status,
        )

    def _create_local_stk_attempt(
        self,
        *,
        user,
        data,
        transaction_type,
        description,
        reference,
        fee_breakdown,
        sacco,
        related_saving,
        related_loan,
    ):
        with db_transaction.atomic():
            provider = self._get_mpesa_provider()
            payment = Transaction.objects.create(
                provider=provider,
                user=user,
                reference=reference,
                transaction_type=transaction_type,
                amount=fee_breakdown['net_amount'],
                gross_amount=fee_breakdown['gross_amount'],
                platform_fee=fee_breakdown['platform_fee'],
                fee_rate=fee_breakdown['fee_rate'],
                sacco=sacco,
                fee_amount=fee_breakdown['platform_fee'],
                status=Transaction.Status.PENDING,
                description=description,
                metadata={
                    'purpose': data['purpose'],
                    'sacco_id': str(data['sacco_id']),
                    'amount': str(fee_breakdown['net_amount']),
                    'net_amount': str(fee_breakdown['net_amount']),
                    'platform_fee': str(fee_breakdown['platform_fee']),
                    'gross_amount': str(fee_breakdown['gross_amount']),
                    'fee_rate': str(fee_breakdown['fee_rate']),
                    'initiation_status': 'LOCAL_RECORDED',
                },
            )
            mpesa_transaction = MpesaTransaction.objects.create(
                transaction=payment,
                phone_number=data['phone_number'],
                related_saving=related_saving,
                related_loan=related_loan,
                related_instalment_number=data.get('instalment_number'),
            )

        return payment, mpesa_transaction

    def _mark_stk_initiation_accepted(
        self,
        payment,
        mpesa_transaction,
        daraja_response,
        merchant_request_id,
        checkout_request_id,
    ):
        with db_transaction.atomic():
            payment = Transaction.objects.select_for_update().get(
                id=payment.id,
            )
            mpesa_transaction = MpesaTransaction.objects.select_for_update().get(
                id=mpesa_transaction.id,
            )
            payment.external_reference = checkout_request_id
            payment.metadata = {
                **payment.metadata,
                'initiation_status': 'ACCEPTED',
                'daraja_response': daraja_response,
            }
            payment.save(
                update_fields=[
                    'external_reference',
                    'metadata',
                    'updated_at',
                ]
            )
            mpesa_transaction.merchant_request_id = merchant_request_id
            mpesa_transaction.checkout_request_id = checkout_request_id
            mpesa_transaction.save(
                update_fields=[
                    'merchant_request_id',
                    'checkout_request_id',
                    'updated_at',
                ]
            )

    def _mark_stk_initiation_failed(
        self,
        payment,
        mpesa_transaction,
        exc,
        *,
        status_unknown=False,
        daraja_response=None,
    ):
        metadata = {
            **payment.metadata,
            'initiation_status': (
                'UNKNOWN' if status_unknown else 'FAILED'
            ),
            'initiation_error': {
                'message': exc.message,
                'response_code': exc.response_code,
                'status_unknown': status_unknown,
            },
        }
        if daraja_response is not None:
            metadata['daraja_response'] = daraja_response

        with db_transaction.atomic():
            payment = Transaction.objects.select_for_update().get(
                id=payment.id,
            )
            mpesa_transaction = MpesaTransaction.objects.select_for_update().get(
                id=mpesa_transaction.id,
            )
            payment.status = Transaction.Status.INITIATION_FAILED
            payment.metadata = metadata
            payment.save(
                update_fields=['status', 'metadata', 'updated_at']
            )
            mpesa_transaction.result_code = exc.response_code
            mpesa_transaction.result_description = exc.message
            mpesa_transaction.save(
                update_fields=[
                    'result_code',
                    'result_description',
                    'updated_at',
                ]
            )

    def _amount_description(self, transaction_type):
        if transaction_type == Transaction.TransactionType.DEPOSIT:
            return 'saving deposit'

        return 'loan repayment'

    def _build_reference(self):
        return f'SS-{uuid4().hex[:20].upper()}'

    def _get_mpesa_provider(self):
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        return provider

    def _get_owned_saving(self, user, data):
        return get_object_or_404(
            Saving.objects.select_related('membership', 'membership__sacco'),
            id=data['saving_id'],
            membership__user=user,
            membership__sacco_id=data['sacco_id'],
        )

    def _get_owned_loan(self, user, data):
        return get_object_or_404(
            Loan.objects.select_related('membership', 'membership__sacco'),
            id=data['loan_id'],
            membership__user=user,
            membership__sacco_id=data['sacco_id'],
        )


class STKStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Get STK transaction status by checkout request id.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request, checkout_request_id):
        mpesa_transaction = get_object_or_404(
            MpesaTransaction.objects.select_related('transaction'),
            checkout_request_id=checkout_request_id,
            transaction__user=request.user,
        )
        transaction = mpesa_transaction.transaction

        # Trigger live query if transaction is PENDING past grace period
        if (
            transaction.status == Transaction.Status.PENDING
            and not mpesa_transaction.callback_received
            and mpesa_transaction.checkout_request_id
        ):
            grace_period_minutes = 2
            cutoff = timezone.now() - timezone.timedelta(minutes=grace_period_minutes)
            
            if transaction.created_at < cutoff:
                try:
                    from .integrations.mpesa.daraja import DarajaClient
                    from .tasks import _process_daraja_status_response
                    
                    daraja_response = DarajaClient().query_stk_status(
                        checkout_request_id,
                    )
                    
                    # Process response using shared function
                    with db_transaction.atomic():
                        mpesa_transaction = MpesaTransaction.objects.select_for_update(
                            of=('self',),
                        ).select_related('transaction').get(
                            id=mpesa_transaction.id,
                        )
                        transaction = mpesa_transaction.transaction
                        
                        # Only process if still PENDING (race condition check)
                        if transaction.status == Transaction.Status.PENDING:
                            _process_daraja_status_response(
                                mpesa_transaction,
                                transaction,
                                daraja_response,
                            )
                            
                            # Refresh for response
                            mpesa_transaction.refresh_from_db()
                            transaction.refresh_from_db()
                except Exception as exc:
                    logger.warning(
                        'Live STK status query failed for checkout_request_id=%s: %s',
                        checkout_request_id,
                        exc,
                    )
                    # Continue with local state if query fails

        return Response(
            {
                'checkout_request_id': mpesa_transaction.checkout_request_id,
                'merchant_request_id': mpesa_transaction.merchant_request_id,
                'status': mpesa_transaction.transaction.status,
                'result_code': mpesa_transaction.result_code,
                'result_description': mpesa_transaction.result_description,
                'callback_received': mpesa_transaction.callback_received,
            },
            status=status.HTTP_200_OK,
        )


class FeePreviewView(APIView):
    """Return a human-readable fee breakdown for a given transaction type.

    Query params: ?type=deposit|repayment|disbursement|withdrawal&amount=1000
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        tx_type = request.query_params.get('type')
        try:
            amount = Decimal(request.query_params.get('amount', '0'))
        except Exception:
            return Response({'detail': 'Invalid amount'}, status=status.HTTP_400_BAD_REQUEST)

        calc = SaccoInvoiceFeeCalculator()
        breakdown = calc.calculate(tx_type, amount)

        if tx_type in ('deposit', 'repayment'):
            summary = {
                'you_pay': f"KES {breakdown['gross_amount']:,.2f}",
                'fee_line': f"Includes KES {breakdown['platform_fee']:,.2f} platform fee",
                'sacco_receives': f"KES {breakdown['gross_amount']:,.2f}",
                'credited_to_you': f"KES {breakdown['net_amount']:,.2f}",
                'note': 'The platform fee is included in your payment.',
            }
        else:
            summary = {
                'amount_approved': f"KES {breakdown['gross_amount']:,.2f}",
                'platform_fee': f"KES {breakdown['platform_fee']:,.2f}",
                'you_receive': f"KES {breakdown['net_amount']:,.2f}",
                'note': 'Platform fee deducted from disbursed amount.',
            }

        return Response({**breakdown, 'summary': summary})


class MPesaSTKCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description='Receive Safaricom STK callback payload.',
        responses={200: openapi.Response('Accepted'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def post(self, request):
        try:
            try:
                callback_body = json.loads(request.body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.error(
                    'M-Pesa STK callback JSON decode error: %s',
                    exc,
                )
                self._log_rejected_callback(
                    request,
                    'INVALID_JSON',
                    str(exc),
                )
                return JsonResponse({'detail': 'Invalid JSON'}, status=400)

            request._mpesa_callback_body = callback_body
            stk_callback = self._get_stk_callback(callback_body)
            checkout_request_id = stk_callback.get('CheckoutRequestID')

            if not checkout_request_id:
                logger.debug('M-Pesa STK callback missing CheckoutRequestID')
                self._log_rejected_callback(
                    request,
                    'MISSING_CHECKOUT_ID',
                    'CheckoutRequestID not found in payload',
                )
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if not is_safaricom_ip(request):
                logger.warning(
                    'M-Pesa STK callback rejected: non-Safaricom IP'
                )
                self._log_rejected_callback(
                    request,
                    'INVALID_IP',
                    'Request not from Safaricom IP range',
                )
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            if is_replay_attack(checkout_request_id):
                logger.warning(
                    'M-Pesa STK callback is replay attack: %s',
                    checkout_request_id,
                )
                self._log_rejected_callback(
                    request,
                    'REPLAY_ATTACK',
                    f'Duplicate checkout_request_id: {checkout_request_id}',
                )
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if not verify_mpesa_signature(request):
                logger.warning(
                    'M-Pesa STK callback signature verification failed'
                )
                self._log_rejected_callback(
                    request,
                    'INVALID_SIGNATURE',
                    'Signature verification failed',
                )
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            try:
                mpesa_transaction = MpesaTransaction.objects.select_related(
                    'transaction',
                ).get(
                    checkout_request_id=checkout_request_id,
                )
            except Exception as exc:
                logger.error(
                    'M-Pesa STK callback transaction lookup error: %s',
                    exc,
                    exc_info=True,
                )
                self._log_rejected_callback(
                    request,
                    'TRANSACTION_NOT_FOUND',
                    f'No transaction for checkout_request_id: {checkout_request_id}',
                )
                _clear_mpesa_replay_marker(checkout_request_id)
                return _retry_mpesa_response()

            # Persist callback to Callback table before enqueuing task
            provider = _get_mpesa_provider_record()
            callback = Callback.objects.create(
                transaction=mpesa_transaction.transaction,
                provider=provider,
                raw_payload=callback_body,
                processed=False,
            )

            from .tasks import process_stk_callback_task

            try:
                process_stk_callback_task.delay(str(callback.id))
            except BROKER_CONNECTION_ERRORS as exc:
                logger.error(
                    'M-Pesa STK callback task enqueue error: %s',
                    exc,
                    exc_info=True,
                )
                callback.processing_error = str(exc)
                callback.save(update_fields=['processing_error'])
                _clear_mpesa_replay_marker(checkout_request_id)
                return _retry_mpesa_response()

            logger.info(
                'M-Pesa STK callback persisted and enqueued: %s',
                checkout_request_id,
            )
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        except Exception as exc:
            logger.error(
                'M-Pesa STK callback unexpected error: %s',
                exc,
                exc_info=True,
            )
            self._log_rejected_callback(
                request,
                'UNEXPECTED_ERROR',
                str(exc),
            )
            return _retry_mpesa_response()

    def _log_rejected_callback(self, request, rejection_reason, details):
        """Log rejected callback payloads for forensics."""
        try:
            callback_body = json.loads(request.body.decode('utf-8'))
        except Exception:
            callback_body = {'raw_body': request.body.decode('utf-8', errors='ignore')}

        logger.warning(
            'M-Pesa STK callback rejected: reason=%s, details=%s, '
            'client_ip=%s, payload_preview=%s',
            rejection_reason,
            details,
            _get_client_ip(request),
            str(callback_body)[:500],
        )

    def _get_stk_callback(self, callback_body):
        body = callback_body.get('Body') or callback_body.get('body') or {}
        return body.get('stkCallback') or body.get('StkCallback') or {}


class B2CDisbursementView(APIView):
    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    @swagger_auto_schema(
        operation_description='Initiate M-Pesa B2C loan disbursement.',
        request_body=B2CDisbursementSerializer,
        responses={201: openapi.Response('Created'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def post(self, request):
        serializer = B2CDisbursementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        current_sacco = getattr(request, 'current_sacco', None)

        if current_sacco is None:
            return Response(
                {'detail': 'SACCO context is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        loan = get_object_or_404(
            Loan.objects.select_related('membership', 'membership__sacco'),
            id=data['loan_id'],
            membership__sacco=current_sacco,
        )

        if loan.status != Loan.Status.APPROVED:
            return Response(
                {'detail': 'Only approved loans can be disbursed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_complete, reason = check_loan_guarantors_complete(loan)
        if not is_complete:
            return Response(
                {'detail': reason},
                status=status.HTTP_400_BAD_REQUEST,
            )

        remarks = data['remarks']

        _success, payload, http_status = initiate_b2c_loan_disbursement(
            loan=loan,
            phone_number=data['phone_number'],
            amount=data['amount'],
            remarks=remarks,
        )

        return Response(payload, status=http_status)


class B2CCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description='Receive Safaricom B2C callback payload.',
        responses={200: openapi.Response('Accepted'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def post(self, request):
        try:
            try:
                callback_body = json.loads(request.body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.error('M-Pesa B2C callback JSON decode error: %s', exc)
                self._log_rejected_callback(
                    request,
                    'INVALID_JSON',
                    str(exc),
                )
                return JsonResponse({'detail': 'Invalid JSON'}, status=400)

            request._mpesa_callback_body = callback_body
            result = (
                callback_body.get('Result')
                or callback_body.get('result')
                or {}
            )
            conversation_id = result.get('ConversationID')

            if not conversation_id:
                logger.debug('M-Pesa B2C callback missing ConversationID')
                self._log_rejected_callback(
                    request,
                    'MISSING_CONVERSATION_ID',
                    'ConversationID not found in payload',
                )
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if not is_safaricom_ip(request):
                logger.warning('M-Pesa B2C callback rejected: non-Safaricom IP')
                self._log_rejected_callback(
                    request,
                    'INVALID_IP',
                    'Request not from Safaricom IP range',
                )
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            if is_replay_attack(conversation_id):
                logger.warning(
                    'M-Pesa B2C callback is replay attack: %s',
                    conversation_id,
                )
                self._log_rejected_callback(
                    request,
                    'REPLAY_ATTACK',
                    f'Duplicate conversation_id: {conversation_id}',
                )
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if not verify_mpesa_signature(request):
                logger.warning(
                    'M-Pesa B2C callback signature verification failed'
                )
                self._log_rejected_callback(
                    request,
                    'INVALID_SIGNATURE',
                    'Signature verification failed',
                )
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            result_code = result.get('ResultCode')

            try:
                mpesa_transaction = MpesaTransaction.objects.select_related(
                    'transaction',
                ).get(
                    conversation_id=conversation_id,
                    transaction_type=MpesaTransaction.TransactionType.B2C,
                )
            except Exception as exc:
                logger.error(
                    'M-Pesa B2C callback transaction lookup error: %s',
                    exc,
                    exc_info=True,
                )
                self._log_rejected_callback(
                    request,
                    'TRANSACTION_NOT_FOUND',
                    f'No transaction for conversation_id: {conversation_id}',
                )
                _clear_mpesa_replay_marker(conversation_id)
                return _retry_mpesa_response()

            # Persist callback to Callback table before enqueuing task
            provider = _get_mpesa_provider_record()
            callback = Callback.objects.create(
                transaction=mpesa_transaction.transaction,
                provider=provider,
                raw_payload=callback_body,
                processed=False,
            )

            try:
                if (
                    mpesa_transaction.related_loan_id
                    and mpesa_transaction.related_loan.disbursement_transaction_id
                ):
                    from services.tasks import on_disbursement_b2c_callback

                    on_disbursement_b2c_callback.delay(
                        str(mpesa_transaction.related_loan_id),
                        result,
                    )
                else:
                    from .tasks import process_b2c_callback_task

                    process_b2c_callback_task.delay(str(callback.id))
            except BROKER_CONNECTION_ERRORS as exc:
                logger.error(
                    'M-Pesa B2C callback task enqueue error: %s',
                    exc,
                    exc_info=True,
                )
                callback.processing_error = str(exc)
                callback.save(update_fields=['processing_error'])
                _clear_mpesa_replay_marker(conversation_id)
                return _retry_mpesa_response()

            logger.info(
                'M-Pesa B2C callback persisted and enqueued: %s',
                conversation_id,
            )
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        except Exception as exc:
            logger.error(
                'M-Pesa B2C callback unexpected error: %s',
                exc,
                exc_info=True,
            )
            self._log_rejected_callback(
                request,
                'UNEXPECTED_ERROR',
                str(exc),
            )
            return _retry_mpesa_response()

    def _log_rejected_callback(self, request, rejection_reason, details):
        """Log rejected callback payloads for forensics."""
        try:
            callback_body = json.loads(request.body.decode('utf-8'))
        except Exception:
            callback_body = {'raw_body': request.body.decode('utf-8', errors='ignore')}

        logger.warning(
            'M-Pesa B2C callback rejected: reason=%s, details=%s, '
            'client_ip=%s, payload_preview=%s',
            rejection_reason,
            details,
            _get_client_ip(request),
            str(callback_body)[:500],
        )


class B2CStatusView(APIView):
    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    @swagger_auto_schema(
        operation_description='Get B2C disbursement status by conversation id.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request, conversation_id):
        mpesa_transaction = get_object_or_404(
            self._get_queryset(request),
            conversation_id=conversation_id,
        )
        return Response(self._serialize_b2c(mpesa_transaction), status=200)

    def _get_queryset(self, request):
        current_sacco = getattr(request, 'current_sacco', None)
        return MpesaTransaction.objects.select_related(
            'transaction',
            'related_loan',
            'related_loan__membership',
        ).filter(
            transaction_type=MpesaTransaction.TransactionType.B2C,
            related_loan__membership__sacco=current_sacco,
        )

    def _serialize_b2c(self, mpesa_transaction):
        return {
            'conversation_id': mpesa_transaction.conversation_id,
            'originator_conversation_id': (
                mpesa_transaction.originator_conversation_id
            ),
            'status': mpesa_transaction.transaction.status,
            'result_code': mpesa_transaction.result_code,
            'result_description': mpesa_transaction.result_description,
            'mpesa_receipt_number': mpesa_transaction.mpesa_receipt_number,
            'callback_received': mpesa_transaction.callback_received,
            'loan_id': str(mpesa_transaction.related_loan_id),
            'amount': mpesa_transaction.transaction.amount,
            'created_at': mpesa_transaction.created_at.isoformat(),
        }


class B2CHistoryView(B2CStatusView):
    @swagger_auto_schema(
        operation_description='List B2C disbursement history for current SACCO context.',
        responses={200: openapi.Response('OK'), 400: 'Bad Request', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
    )
    def get(self, request):
        history = [
            self._serialize_b2c(mpesa_transaction)
            for mpesa_transaction in self._get_queryset(request).order_by(
                '-created_at'
            )
        ]
        return Response(history, status=200)


class CallbackCreateView(StandardResponseMixin, CreateAPIView):
    serializer_class = CallbackSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.validated_data['provider']

        if not self._verify_callback(request, provider):
            return Response(
                {'detail': 'Forbidden'},
                status=status.HTTP_403_FORBIDDEN,
            )

        callback = serializer.save()
        process_payment_callback.delay(str(callback.id))
        data = CallbackSerializer(callback).data
        return self.created(data, 'Callback received')

    def _verify_callback(self, request, provider):
        if provider.provider_type == PaymentProvider.ProviderType.MPESA:
            payload = request.data.get('raw_payload') or request.data
            request._mpesa_callback_body = payload
            if not is_safaricom_ip(request):
                logger.warning(
                    'M-Pesa callback rejected from non-Safaricom IP: %s',
                    request.META.get('REMOTE_ADDR'),
                )
                return False
            return verify_mpesa_signature(request)

        try:
            provider_class = get_provider_class(provider.name)
            provider_client = provider_class()
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            logger.warning(
                'Callback rejected for unsupported provider %s: %s',
                provider.name,
                exc,
            )
            return False

        try:
            return provider_client.verify_webhook(request)
        except Exception as exc:
            logger.warning(
                'Callback verification failed for provider %s: %s',
                provider.name,
                exc,
            )
            return False


