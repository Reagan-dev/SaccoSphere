import json
import logging
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Sacco
from accounts.permissions import IsSaccoAdmin
from config.response import StandardResponseMixin
from guarantor.utils import check_loan_guarantors_complete
from payments.providers import get_psp_provider
from services.models import Loan, Saving

from .integrations.mpesa.daraja import DarajaClient, DarajaError
from .fee_calculator import SaccoInvoiceFeeCalculator

logger = logging.getLogger('saccosphere.payments')
from .integrations.mpesa.security import (
    is_replay_attack,
    is_safaricom_ip,
    verify_mpesa_signature,
)
from .models import Callback, MpesaTransaction, PaymentProvider, Transaction
from .serializers import (
    CallbackSerializer,
    MpesaTransactionSerializer,
    TransactionSerializer,
)
from .tasks import process_payment_callback
from .validators import validate_mpesa_phone


class DepositRequestSerializer(serializers.Serializer):
    """Validate the fields required to initiate a PSP-backed deposit."""

    phone_number = serializers.CharField(max_length=15)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    sacco_id = serializers.PrimaryKeyRelatedField(
        source='sacco',
        queryset=Sacco.objects.all(),
    )

    def validate_phone_number(self, value):
        return validate_mpesa_phone(value)

    def validate_amount(self, value):
        if value <= Decimal('0.00'):
            raise serializers.ValidationError(
                'Amount must be greater than zero.'
            )

        if value > Decimal('300000.00'):
            raise serializers.ValidationError(
                'Amount cannot be more than 300000.'
            )

        return value

    def validate(self, data):
        """Compute fee breakdown and attach to validated data.

        The frontend expects to preview the gross amount (what the member will
        be charged), the platform fee, and the net amount that will be
        credited to savings.
        """
        net_amount = data['amount']
        fee_rate = getattr(settings, 'PLATFORM_FEES', {}).get('deposit')
        from decimal import Decimal as _Decimal

        if fee_rate is None:
            fee_rate = _Decimal('0.01')
        else:
            fee_rate = _Decimal(str(fee_rate))

        platform_fee = (net_amount * fee_rate).quantize(_Decimal('0.01'))
        gross_amount = (net_amount + platform_fee).quantize(_Decimal('0.01'))

        data['net_amount'] = net_amount
        data['platform_fee'] = platform_fee
        data['gross_amount'] = gross_amount
        data['fee_rate'] = fee_rate

        return data


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
                    amount=data['gross_amount'],
                    sacco=sacco,
                    net_amount=data['net_amount'],
                    platform_fee=data['platform_fee'],
                )
                transaction.external_reference = result.provider_reference
                transaction.save(update_fields=['external_reference', 'updated_at'])
        except Exception:
            logger.exception('Deposit initiation failed for transaction %s', transaction.id)
            try:
                with db_transaction.atomic():
                    transaction.status = Transaction.Status.FAILED
                    transaction.save(update_fields=['status', 'updated_at'])
            except Exception:
                logger.exception('Failed to update transaction %s to FAILED', transaction.id)
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

        reference = self._build_reference()

        amount = data['amount']

        try:
            daraja_response = DarajaClient().initiate_stk_push(
                phone_number=data['phone_number'],
                amount=amount,
                account_reference=reference,
                description=description,
                callback_path='/api/v1/payments/callback/mpesa/stk/',
            )
        except DarajaError as exc:
            logger.error(
                'M-Pesa STK push failed for user %s: %s (code=%s)',
                request.user.email,
                exc.message,
                exc.response_code,
                exc_info=True,
            )
            return Response(
                {
                    'error': exc.message,
                    'response_code': exc.response_code,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        merchant_request_id = daraja_response.get('MerchantRequestID')
        checkout_request_id = daraja_response.get('CheckoutRequestID')

        with db_transaction.atomic():
            provider = self._get_mpesa_provider()
            payment = Transaction.objects.create(
                provider=provider,
                user=request.user,
                reference=reference,
                external_reference=checkout_request_id,
                transaction_type=transaction_type,
                amount=amount,
                status=Transaction.Status.PENDING,
                description=description,
                metadata={
                    'purpose': data['purpose'],
                    'sacco_id': str(data['sacco_id']),
                    'amount': str(amount),
                    'daraja_response': daraja_response,
                },
            )
            MpesaTransaction.objects.create(
                transaction=payment,
                phone_number=data['phone_number'],
                merchant_request_id=merchant_request_id,
                checkout_request_id=checkout_request_id,
                related_saving=related_saving,
                related_loan=related_loan,
                related_instalment_number=data.get('instalment_number'),
            )

        # Build clear message showing total charge breakdown
        if transaction_type == Transaction.TransactionType.DEPOSIT:
            amount_description = f'saving deposit'
        else:
            amount_description = f'loan repayment'

        message = (
            f'Check your phone to enter your M-Pesa PIN. '
            f'You will be charged KES {amount} for {amount_description}.'
        )

        return Response(
            {
                'checkout_request_id': checkout_request_id,
                'merchant_request_id': merchant_request_id,
                'amount': str(amount),
                'message': message,
            },
            status=status.HTTP_201_CREATED,
        )

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
        )

        if mpesa_transaction.transaction.user_id != request.user.id:
            return Response(
                {'detail': 'You do not have permission to view this status.'},
                status=status.HTTP_403_FORBIDDEN,
            )

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
            if not is_safaricom_ip(request):
                logger.warning(
                    'M-Pesa STK callback rejected: non-Safaricom IP'
                )
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            try:
                callback_body = json.loads(request.body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.error(
                    'M-Pesa STK callback JSON decode error: %s',
                    exc,
                )
                return JsonResponse({'detail': 'Invalid JSON'}, status=400)

            request._mpesa_callback_body = callback_body
            stk_callback = self._get_stk_callback(callback_body)
            checkout_request_id = stk_callback.get('CheckoutRequestID')

            if not checkout_request_id:
                logger.debug('M-Pesa STK callback missing CheckoutRequestID')
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if is_replay_attack(checkout_request_id):
                logger.warning(
                    'M-Pesa STK callback is replay attack: %s',
                    checkout_request_id,
                )
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if not verify_mpesa_signature(request):
                logger.warning(
                    'M-Pesa STK callback signature verification failed'
                )
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            # Check if transaction exists, with short timeout
            try:
                transaction_exists = MpesaTransaction.objects.filter(
                    checkout_request_id=checkout_request_id,
                ).exists()
            except Exception as exc:
                logger.error(
                    'M-Pesa STK callback transaction lookup error: %s',
                    exc,
                    exc_info=True,
                )
                # Accept callback to prevent retry, but log error
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if not transaction_exists:
                logger.debug(
                    'M-Pesa STK callback transaction not found: %s',
                    checkout_request_id,
                )
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            result_code = stk_callback.get('ResultCode')

            from .tasks import process_stk_callback_task

            try:
                process_stk_callback_task.delay(
                    checkout_request_id,
                    result_code,
                    callback_body,
                )
            except Exception as exc:
                logger.error(
                    'M-Pesa STK callback task enqueue error: %s',
                    exc,
                    exc_info=True,
                )
                # Still return success to prevent M-Pesa retry
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            logger.info(
                'M-Pesa STK callback enqueued: %s',
                checkout_request_id,
            )
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        except Exception as exc:
            logger.error(
                'M-Pesa STK callback unexpected error: %s',
                exc,
                exc_info=True,
            )
            # Return success anyway to prevent M-Pesa from retrying
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

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

        reference = self._build_reference()
        remarks = data['remarks']
        callback_url = DarajaClient()._build_callback_url(
            '/api/v1/payments/callback/mpesa/b2c/'
        )

        try:
            daraja_response = DarajaClient().initiate_b2c(
                phone_number=data['phone_number'],
                amount=data['amount'],
                occasion='Loan Disbursement',
                remarks=remarks,
                result_url=callback_url,
                timeout_url=callback_url,
            )
        except DarajaError as exc:
            return Response(
                {
                    'error': exc.message,
                    'response_code': exc.response_code,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        conversation_id = daraja_response.get('ConversationID')
        originator_conversation_id = daraja_response.get(
            'OriginatorConversationID'
        )

        with db_transaction.atomic():
            provider = self._get_mpesa_provider()
            payment = Transaction.objects.create(
                provider=provider,
                user=loan.membership.user,
                reference=reference,
                external_reference=conversation_id,
                transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
                amount=data['amount'],
                status=Transaction.Status.PENDING,
                description=remarks,
                metadata={'daraja_response': daraja_response},
            )
            MpesaTransaction.objects.create(
                transaction=payment,
                phone_number=data['phone_number'],
                conversation_id=conversation_id,
                originator_conversation_id=originator_conversation_id,
                transaction_type=MpesaTransaction.TransactionType.B2C,
                related_loan=loan,
            )
            loan.status = Loan.Status.DISBURSEMENT_PENDING
            loan.save(update_fields=['status', 'updated_at'])

        return Response(
            {
                'conversation_id': conversation_id,
                'message': 'Disbursement initiated.',
            },
            status=status.HTTP_201_CREATED,
        )

    def _build_reference(self):
        return f'SS-B2C-{uuid4().hex[:18].upper()}'

    def _get_mpesa_provider(self):
        provider, _ = PaymentProvider.objects.get_or_create(
            name='M-Pesa',
            defaults={
                'provider_type': PaymentProvider.ProviderType.MPESA,
                'is_active': True,
            },
        )
        return provider


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
            if not is_safaricom_ip(request):
                logger.warning('M-Pesa B2C callback rejected: non-Safaricom IP')
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            try:
                callback_body = json.loads(request.body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.error('M-Pesa B2C callback JSON decode error: %s', exc)
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
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if is_replay_attack(conversation_id):
                logger.warning(
                    'M-Pesa B2C callback is replay attack: %s',
                    conversation_id,
                )
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            if not verify_mpesa_signature(request):
                logger.warning(
                    'M-Pesa B2C callback signature verification failed'
                )
                return JsonResponse({'detail': 'Forbidden'}, status=403)

            result_code = result.get('ResultCode')

            from .tasks import process_b2c_callback_task

            try:
                process_b2c_callback_task.delay(
                    conversation_id,
                    result_code,
                    callback_body,
                )
            except Exception as exc:
                logger.error(
                    'M-Pesa B2C callback task enqueue error: %s',
                    exc,
                    exc_info=True,
                )
                # Still return success to prevent M-Pesa retry
                return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

            logger.info(
                'M-Pesa B2C callback enqueued: %s',
                conversation_id,
            )
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        except Exception as exc:
            logger.error(
                'M-Pesa B2C callback unexpected error: %s',
                exc,
                exc_info=True,
            )
            # Return success anyway to prevent M-Pesa from retrying
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


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
        # Apply M-Pesa IP verification for M-Pesa callbacks
        provider_id = request.data.get('provider')
        if provider_id:
            try:
                provider = PaymentProvider.objects.get(id=provider_id)
                if (
                    provider.provider_type == PaymentProvider.ProviderType.MPESA
                    and not is_safaricom_ip(request)
                ):
                    logger.warning(
                        'M-Pesa callback rejected from non-Safaricom IP: %s',
                        request.META.get('REMOTE_ADDR'),
                    )
                    return Response(
                        {'detail': 'Forbidden'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except PaymentProvider.DoesNotExist:
                pass

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        callback = serializer.save()
        data = CallbackSerializer(callback).data
        return self.created(data, 'Callback received')


