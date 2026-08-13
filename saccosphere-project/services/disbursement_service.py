"""Fraud-aware loan disbursement orchestration."""

from uuid import uuid4

from django.db import transaction as db_transaction
from django.utils import timezone

from payments.models import MpesaTransaction, PaymentProvider, Transaction
from payments.providers import get_psp_provider

from .models import DisbursementAuditLog, Loan


class DisbursementService:
    """
    Orchestrate loan disbursement with fee-aware payout and audit evidence.

    SACCOs send members the net amount and retain the platform fee. SaccoSphere
    invoices that fee only after member or M-Pesa delivery confirmation.
    """

    def initiate(self, loan, admin_user, request) -> dict:
        """
        Initiate M-Pesa B2C for the net amount and store the ConversationID.
        """
        from payments.fee_calculator import SaccoInvoiceFeeCalculator

        with db_transaction.atomic():
            loan = (
                Loan.objects.select_for_update()
                .select_related('membership', 'membership__sacco', 'membership__user')
                .get(id=loan.id)
            )
            member = loan.membership.user
            sacco = loan.membership.sacco

            if loan.status != Loan.Status.APPROVED:
                raise ValueError('Only approved loans can be disbursed.')

            if loan.disbursement_status != Loan.DisbursementStatus.PENDING:
                raise ValueError(
                    'Cannot disburse: status is '
                    f'{loan.disbursement_status}.'
                )

            if not member.phone_number:
                raise ValueError(
                    'Member phone number is required before disbursement.'
                )

            DisbursementAuditLog.objects.create(
                loan=loan,
                event='LOAN_APPROVED',
                actor=admin_user,
                actor_role='sacco_admin',
                ip_address=self._get_ip(request),
                details={
                    'loan_amount': str(loan.amount),
                    'member_id': str(member.id),
                    'approved_by': str(admin_user.id),
                },
            )

            calc = SaccoInvoiceFeeCalculator()
            breakdown = calc.calculate('disbursement', loan.amount)
            provider = get_psp_provider(sacco=sacco)
            payment_provider = self._get_payment_provider(provider)

            tx = Transaction.objects.create(
                provider=payment_provider,
                sacco=sacco,
                user=member,
                reference=self._build_transaction_reference(),
                transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
                amount=breakdown['net_amount'],
                gross_amount=breakdown['gross_amount'],
                platform_fee=breakdown['platform_fee'],
                fee_rate=None,
                status=Transaction.Status.PENDING,
                description=f'Loan disbursement - {loan.id}',
                metadata={'loan_id': str(loan.id)},
            )
            mpesa_transaction = MpesaTransaction.objects.create(
                transaction=tx,
                phone_number=member.phone_number,
                transaction_type=MpesaTransaction.TransactionType.B2C,
                related_loan=loan,
            )

            result = provider.disburse(
                transaction_id=str(tx.id),
                phone=member.phone_number,
                amount=breakdown['net_amount'],
                reference=f'LOAN-{loan.id}',
            )

            if not result.success:
                tx.status = Transaction.Status.FAILED
                tx.metadata = {
                    **tx.metadata,
                    'disbursement_error': result.error_message,
                    'provider_response': result.raw_response,
                }
                tx.save(update_fields=['status', 'metadata', 'updated_at'])
                loan.disbursement_status = Loan.DisbursementStatus.FAILED
                loan.save(update_fields=['disbursement_status', 'updated_at'])
                raise ValueError(
                    result.error_message or 'Disbursement provider failed.'
                )

            conversation_id = result.conversation_id
            tx.status = Transaction.Status.SENT
            tx.external_reference = conversation_id
            tx.metadata = {
                **tx.metadata,
                'provider_response': result.raw_response,
            }
            tx.save(
                update_fields=[
                    'status',
                    'external_reference',
                    'metadata',
                    'updated_at',
                ],
            )

            mpesa_transaction.conversation_id = conversation_id
            mpesa_transaction.save(
                update_fields=['conversation_id', 'updated_at'],
            )

            loan.disbursement_transaction = tx
            loan.mpesa_transaction_record = mpesa_transaction
            loan.mpesa_conversation_id = conversation_id
            loan.disbursement_status = Loan.DisbursementStatus.INITIATED
            loan.disbursement_initiated_at = timezone.now()
            loan.status = Loan.Status.DISBURSEMENT_PENDING
            loan.save(
                update_fields=[
                    'disbursement_transaction',
                    'mpesa_transaction_record',
                    'mpesa_conversation_id',
                    'disbursement_status',
                    'disbursement_initiated_at',
                    'status',
                    'updated_at',
                ],
            )

            DisbursementAuditLog.objects.create(
                loan=loan,
                event='B2C_INITIATED',
                actor=admin_user,
                actor_role='sacco_admin',
                ip_address=self._get_ip(request),
                mpesa_ref=conversation_id,
                details={
                    'conversation_id': conversation_id,
                    'gross_amount': str(breakdown['gross_amount']),
                    'platform_fee': str(breakdown['platform_fee']),
                    'net_amount_sent': str(breakdown['net_amount']),
                },
            )

        return {
            'status': Loan.DisbursementStatus.INITIATED,
            'conversation_id': conversation_id,
            'message': (
                'Disbursement initiated. Awaiting M-Pesa confirmation.'
            ),
        }

    def _get_ip(self, request) -> str:
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    def _get_payment_provider(self, provider):
        provider_name = provider.get_provider_name()
        provider_type = PaymentProvider.ProviderType.MPESA
        if provider_name == 'mock':
            provider_type = PaymentProvider.ProviderType.INTERNAL

        payment_provider, _created = PaymentProvider.objects.get_or_create(
            name=provider_name,
            defaults={
                'provider_type': provider_type,
                'is_active': True,
            },
        )
        return payment_provider

    def _build_transaction_reference(self):
        return f'SS-DSB-{uuid4().hex[:18].upper()}'
