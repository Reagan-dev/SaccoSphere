from decimal import Decimal
from uuid import uuid4

from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.response import StandardResponseMixin
from services.models import Loan, Saving

from .integrations.mpesa.daraja import DarajaClient, DarajaError
from .models import MpesaTransaction, PaymentProvider, Transaction
from .serializers import (
    CallbackSerializer,
    MpesaTransactionSerializer,
    TransactionSerializer,
)
from .validators import validate_mpesa_phone


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

        try:
            daraja_response = DarajaClient().initiate_stk_push(
                phone_number=data['phone_number'],
                amount=data['amount'],
                account_reference=reference,
                description=description,
                callback_path='/api/v1/payments/callbacks/',
            )
        except DarajaError as exc:
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
                amount=data['amount'],
                status=Transaction.Status.PENDING,
                description=description,
                metadata={
                    'purpose': data['purpose'],
                    'sacco_id': str(data['sacco_id']),
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

        return Response(
            {
                'checkout_request_id': checkout_request_id,
                'merchant_request_id': merchant_request_id,
                'message': 'Check your phone to enter your M-Pesa PIN.',
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


class CallbackCreateView(StandardResponseMixin, CreateAPIView):
    serializer_class = CallbackSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        callback = serializer.save()
        data = CallbackSerializer(callback).data
        return self.created(data, 'Callback received')


# ============================================================
# REVIEW — READ THIS THEN DELETE FROM THIS LINE TO THE END
# ============================================================
#
# What each class or function does and why:
# - DarajaClient in payments/integrations/mpesa/daraja.py talks to Safaricom
#   Daraja. It reads credentials from settings, gets and caches an access
#   token, starts STK push requests, and can query STK request status.
# - DarajaError is a custom exception. It lets views catch M-Pesa-specific
#   failures and return 502 without exposing a Python traceback.
# - validate_mpesa_phone in payments/validators.py checks Kenyan M-Pesa phone
#   formats and normalizes them to 2547... or 2541..., which Daraja expects.
# - STKPushRequestSerializer validates the incoming JSON before the view
#   touches the database or calls Daraja.
# - STKPushView starts the M-Pesa prompt, then creates a pending Transaction
#   and MpesaTransaction if Safaricom accepts the request.
# - STKStatusView returns the local pending/completed/failed status for a
#   CheckoutRequestID and blocks users from seeing someone else's transaction.
#
# Django/Python concepts that may be useful:
# - APIView is a Django REST Framework class for custom endpoint logic.
# - serializers.Serializer validates raw request data and gives you safe
#   validated_data.
# - get_object_or_404 returns an object or automatically raises a 404 response.
# - db_transaction.atomic() means the database writes inside it succeed
#   together or roll back together.
# - Django cache stores the Daraja access token for 50 minutes so you do not
#   request a new token on every payment attempt.
#
# One manual test:
# - Log in, get a JWT, then POST to
#   /api/v1/payments/mpesa/stk-push/ with a valid phone number, amount,
#   purpose=SAVING_DEPOSIT, sacco_id, and saving_id. Confirm your phone gets
#   the M-Pesa PIN prompt and the response includes checkout_request_id.
#
# Important design decision:
# - The endpoint verifies that the saving or loan belongs to the logged-in user
#   and the supplied SACCO before calling Daraja. This prevents a member from
#   starting a payment against another member's account.
#
# END OF REVIEW — DELETE EVERYTHING FROM THE FIRST # LINE ABOVE
# ============================================================
