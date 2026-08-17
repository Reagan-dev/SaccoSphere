"""Billing management API views."""

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdminOrSuperAdmin, IsSuperAdmin
from billing.models import Invoice, InvoicePayment, MonthlySaccoInvoice
from billing.serializers import MonthlySaccoInvoiceSerializer
from billing.services import (
    build_invoice_csv,
    build_invoice_pdf,
    send_invoice_to_sacco,
)
from billing.tasks import send_payment_received_notice


logger = logging.getLogger('saccosphere.billing')


class MonthlyInvoiceListView(ListAPIView):
    """List monthly invoices visible to current SACCO admin or super admin."""

    serializer_class = MonthlySaccoInvoiceSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]

    def get_queryset(self):
        queryset = MonthlySaccoInvoice.objects.select_related('sacco')
        user = self.request.user
        if user.is_staff or user.roles.filter(name='SUPER_ADMIN').exists():
            return queryset

        admin_sacco_ids = user.roles.filter(
            name='SACCO_ADMIN',
            sacco__isnull=False,
        ).values_list('sacco_id', flat=True)
        return queryset.filter(sacco_id__in=admin_sacco_ids)


class MonthlyInvoiceDetailView(RetrieveAPIView):
    """Retrieve one invoice by id."""

    serializer_class = MonthlySaccoInvoiceSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]
    lookup_field = 'id'

    def get_queryset(self):
        queryset = MonthlySaccoInvoice.objects.select_related('sacco')
        user = self.request.user
        if user.is_staff or user.roles.filter(name='SUPER_ADMIN').exists():
            return queryset

        admin_sacco_ids = user.roles.filter(
            name='SACCO_ADMIN',
            sacco__isnull=False,
        ).values_list('sacco_id', flat=True)
        return queryset.filter(sacco_id__in=admin_sacco_ids)


class MonthlyInvoiceResendView(APIView):
    """Resend existing invoice report to SACCO emails/admin recipients."""

    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]

    def post(self, request, invoice_id):
        invoice = get_object_or_404(MonthlySaccoInvoice, id=invoice_id)
        self.check_object_permissions(request, invoice)
        sent = send_invoice_to_sacco(invoice)
        if not sent:
            return Response(
                {'detail': 'No invoice recipients configured for this SACCO.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'detail': 'Invoice resent successfully.'},
            status=status.HTTP_200_OK,
        )


class MonthlyInvoiceDownloadView(APIView):
    """Download invoice report as CSV or PDF."""

    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]

    def get(self, request, invoice_id):
        invoice = get_object_or_404(MonthlySaccoInvoice, id=invoice_id)
        self.check_object_permissions(request, invoice)

        file_format = request.query_params.get('format', 'csv').lower()
        if file_format == 'pdf':
            content = build_invoice_pdf(invoice)
            response = HttpResponse(content, content_type='application/pdf')
            response['Content-Disposition'] = (
                f'attachment; filename=invoice-{invoice.id}.pdf'
            )
            return response

        content = build_invoice_csv(invoice)
        response = HttpResponse(content, content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename=invoice-{invoice.id}.csv'
        )
        return response


class InvoiceMarkPaidView(APIView):
    """Record invoice payment and immediately restore SACCO admin access."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, invoice_id):
        payment_ref = request.data.get('payment_ref', '').strip()
        payment_method = request.data.get('payment_method', '').strip()
        amount = self._parse_amount(request.data.get('amount'))

        if amount is None:
            return Response(
                {'amount': 'A valid decimal amount is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not payment_ref:
            return Response(
                {'payment_ref': 'This field is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_methods = dict(InvoicePayment.METHODS)
        if payment_method not in valid_methods:
            return Response(
                {
                    'payment_method': (
                        f'Must be one of: {", ".join(valid_methods)}.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            queryset = Invoice.objects.select_for_update().select_related(
                'sacco',
            )
            invoice = get_object_or_404(queryset, id=invoice_id)

            if amount < invoice.total_amount:
                return Response(
                    {
                        'amount': (
                            'Payment amount must be greater than or equal '
                            'to the invoice total.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payment = InvoicePayment.objects.create(
                invoice=invoice,
                amount=amount,
                payment_method=payment_method,
                payment_ref=payment_ref,
                recorded_by=request.user,
            )

            invoice.status = 'paid'
            invoice.paid_at = timezone.now()
            invoice.payment_reference = payment_ref
            invoice.save(
                update_fields=[
                    'status',
                    'paid_at',
                    'payment_reference',
                    'updated_at',
                ],
            )

            sacco = invoice.sacco
            sacco.is_billing_suspended = False
            sacco.suspended_at = None
            sacco.suspension_reason = ''
            sacco.save(
                update_fields=[
                    'is_billing_suspended',
                    'suspended_at',
                    'suspension_reason',
                    'updated_at',
                ],
            )

        self._send_payment_notice(sacco.id, invoice.id)
        return Response(
            {
                'detail': 'Invoice marked paid. SACCO access restored.',
                'invoice_id': str(invoice.id),
                'payment_id': str(payment.id),
                'status': invoice.status,
            },
            status=status.HTTP_200_OK,
        )

    def _parse_amount(self, value):
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _send_payment_notice(self, sacco_id, invoice_id):
        try:
            send_payment_received_notice.delay(str(sacco_id), str(invoice_id))
        except Exception:
            logger.exception(
                'Failed to enqueue payment received notice for invoice %s.',
                invoice_id,
            )
