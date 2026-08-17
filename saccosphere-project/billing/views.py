"""Billing management API views."""

import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied

from accounts.models import Sacco
from accounts.permissions import (
    IsSaccoAdmin,
    IsSaccoAdminOrSuperAdmin,
    IsSuperAdmin,
)
from billing.models import Invoice, InvoicePayment, MonthlySaccoInvoice
from billing.serializers import (
    CurrentMonthPreviewSerializer,
    InvoiceDetailSerializer,
    InvoiceListSerializer,
    RevenueSummarySerializer,
)
from billing.services import send_invoice_to_sacco
from billing.tasks import send_payment_received_notice
from saccomanagement.models import Role


logger = logging.getLogger('saccosphere.billing')


class InvoiceResponseHelpers:
    """Shared response helpers for invoice API serializers."""

    @staticmethod
    def days_overdue(invoice):
        if invoice.status == 'paid' or invoice.due_date >= timezone.localdate():
            return 0
        return (timezone.localdate() - invoice.due_date).days

    @staticmethod
    def pdf_url(invoice, request):
        if not invoice.pdf_path:
            return ''

        try:
            relative_path = Path(invoice.pdf_path).resolve().relative_to(
                Path(settings.MEDIA_ROOT).resolve(),
            )
        except (OSError, ValueError):
            return ''

        media_url = settings.MEDIA_URL.rstrip('/')
        url = f'{media_url}/{relative_path.as_posix()}'
        if request is None:
            return url
        return request.build_absolute_uri(url)

    @staticmethod
    def by_type_summary(line_items):
        summary = {
            transaction_type: {
                'count': 0,
                'total_fee': Decimal('0.00'),
                'total_gross_amount': Decimal('0.00'),
            }
            for transaction_type, _label in InvoiceLineItemChoices.choices()
        }

        for item in line_items:
            if item.transaction_type not in summary:
                summary[item.transaction_type] = {
                    'count': 0,
                    'total_fee': Decimal('0.00'),
                    'total_gross_amount': Decimal('0.00'),
                }

            summary[item.transaction_type]['count'] += 1
            summary[item.transaction_type]['total_fee'] += item.platform_fee
            summary[item.transaction_type][
                'total_gross_amount'
            ] += item.gross_amount

        return summary


class InvoiceLineItemChoices:
    """Avoid importing private model metadata in multiple views."""

    @staticmethod
    def choices():
        from billing.models import InvoiceLineItem

        return InvoiceLineItem.TRANSACTION_TYPES


class InvoiceAccessMixin:
    """Filter invoices and invoice line items by the current user's SACCOs."""

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['invoice_helpers'] = InvoiceResponseHelpers
        return context

    def is_super_admin(self):
        user = self.request.user
        return (
            user.is_staff
            or user.roles.filter(name=Role.SUPER_ADMIN).exists()
        )

    def admin_sacco_ids(self):
        return self.request.user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
        ).values_list('sacco_id', flat=True)

    def filter_for_user_saccos(self, queryset):
        if self.is_super_admin():
            return queryset
        return queryset.filter(sacco_id__in=self.admin_sacco_ids())

    def enforce_invoice_access(self, invoice):
        if self.is_super_admin():
            return

        if invoice.sacco_id not in set(self.admin_sacco_ids()):
            raise PermissionDenied(
                'You do not have access to this invoice.'
            )


class InvoiceListView(InvoiceAccessMixin, ListAPIView):
    """List invoices visible to the current SACCO admin or super admin."""

    serializer_class = InvoiceListSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]
    pagination_class = None

    def get_queryset(self):
        queryset = Invoice.objects.select_related('sacco')
        queryset = self.filter_for_user_saccos(queryset)

        status_filter = self.request.query_params.get('status')
        billing_month = self.request.query_params.get('billing_month')
        sacco_id = self.request.query_params.get('sacco_id')

        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if billing_month:
            queryset = queryset.filter(billing_month=billing_month)
        if sacco_id and self.is_super_admin():
            queryset = queryset.filter(sacco_id=sacco_id)

        return queryset.order_by('-billing_month')


class InvoiceDetailView(InvoiceAccessMixin, RetrieveAPIView):
    """Retrieve one invoice with line items and transaction-type summary."""

    serializer_class = InvoiceDetailSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]
    lookup_field = 'id'

    def get_queryset(self):
        queryset = Invoice.objects.select_related('sacco').prefetch_related(
            'invoicelineitem_set__transaction',
        )
        return self.filter_for_user_saccos(queryset)

    def get_object(self):
        invoice = get_object_or_404(
            Invoice.objects.select_related('sacco').prefetch_related(
                'invoicelineitem_set__transaction',
            ),
            id=self.kwargs['id'],
        )
        self.enforce_invoice_access(invoice)
        return invoice


class InvoiceDownloadView(InvoiceAccessMixin, APIView):
    """Stream an invoice PDF file."""

    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]

    def get(self, request, invoice_id):
        invoice = get_object_or_404(Invoice.objects.all(), id=invoice_id)
        self.enforce_invoice_access(invoice)

        if not invoice.pdf_path or not Path(invoice.pdf_path).exists():
            return Response(
                {'detail': 'Invoice PDF is not available.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        response = FileResponse(
            open(invoice.pdf_path, 'rb'),
            as_attachment=True,
            filename=f'{invoice.invoice_number}.pdf',
            content_type='application/pdf',
        )
        return response


class RevenueSummaryView(APIView):
    """Return platform revenue summary for super admins."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        today = timezone.localdate()
        this_month = today.replace(day=1)
        last_month = self._previous_month(this_month)
        start_month = self._month_offset(this_month, -11)

        paid_filter = Q(status='paid')
        outstanding_filter = Q(status__in=['sent', 'overdue', 'suspended'])
        money_default = Value(Decimal('0.00'))

        invoices = Invoice.objects.select_related('sacco')
        total_paid = invoices.filter(paid_filter).aggregate(
            total=Coalesce(Sum('total_amount'), money_default),
        )['total']
        revenue_this_month = invoices.filter(
            paid_filter,
            billing_month=this_month,
        ).aggregate(total=Coalesce(Sum('total_amount'), money_default))[
            'total'
        ]
        revenue_last_month = invoices.filter(
            paid_filter,
            billing_month=last_month,
        ).aggregate(total=Coalesce(Sum('total_amount'), money_default))[
            'total'
        ]
        outstanding_invoices = invoices.filter(outstanding_filter)

        by_sacco = []
        sacco_rows = invoices.values('sacco_id', 'sacco__name').annotate(
            total_invoiced=Coalesce(Sum('total_amount'), money_default),
            total_paid=Coalesce(
                Sum('total_amount', filter=paid_filter),
                money_default,
            ),
        ).order_by('sacco__name')
        for row in sacco_rows:
            by_sacco.append(
                {
                    'sacco_name': row['sacco__name'],
                    'total_invoiced': row['total_invoiced'],
                    'total_paid': row['total_paid'],
                    'outstanding': (
                        row['total_invoiced'] - row['total_paid']
                    ),
                }
            )

        by_month = invoices.filter(
            billing_month__gte=start_month,
        ).annotate(month=TruncMonth('billing_month')).values(
            'month',
        ).annotate(
            total_invoiced=Coalesce(Sum('total_amount'), money_default),
            total_paid=Coalesce(
                Sum('total_amount', filter=paid_filter),
                money_default,
            ),
        ).order_by('month')

        data = {
            'total_revenue_all_time': total_paid,
            'revenue_this_month': revenue_this_month,
            'revenue_last_month': revenue_last_month,
            'outstanding_invoices_count': outstanding_invoices.count(),
            'outstanding_invoices_total': outstanding_invoices.aggregate(
                total=Coalesce(Sum('total_amount'), money_default),
            )['total'],
            'overdue_invoices_count': invoices.filter(
                status='overdue',
            ).count(),
            'suspended_saccos_count': Sacco.objects.filter(
                is_billing_suspended=True,
            ).count(),
            'by_sacco': by_sacco,
            'by_month': list(by_month),
        }
        serializer = RevenueSummarySerializer(data)
        return Response(serializer.data)

    def _previous_month(self, month):
        return self._month_offset(month, -1)

    def _month_offset(self, month, offset):
        month_number = month.month + offset
        year = month.year
        while month_number < 1:
            month_number += 12
            year -= 1
        while month_number > 12:
            month_number -= 12
            year += 1
        return month.replace(year=year, month=month_number, day=1)


class CurrentMonthTransactionPreviewView(InvoiceAccessMixin, APIView):
    """Preview uninvoiced current-month SACCO billing line items."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get(self, request):
        billing_month = timezone.localdate().replace(day=1)
        line_items = self.filter_for_user_saccos(
            self._base_line_items_queryset(),
        ).filter(
            billing_month=billing_month,
            invoiced=False,
        )

        projected_total = line_items.aggregate(
            total=Coalesce(Sum('platform_fee'), Value(Decimal('0.00'))),
        )['total']
        by_type = InvoiceResponseHelpers.by_type_summary(line_items)
        data = {
            'billing_month': billing_month,
            'projected_invoice_total': projected_total,
            'transactions_count': line_items.count(),
            'by_type': by_type,
            'note': (
                'This is a preview. Final invoice generated on 1st of '
                'next month.'
            ),
        }
        serializer = CurrentMonthPreviewSerializer(data)
        return Response(serializer.data)

    def _base_line_items_queryset(self):
        from billing.models import InvoiceLineItem

        return InvoiceLineItem.objects.select_related('sacco', 'transaction')


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
