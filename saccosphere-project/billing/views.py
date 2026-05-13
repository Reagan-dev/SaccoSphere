"""Billing management API views."""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdminOrSuperAdmin
from billing.models import MonthlySaccoInvoice
from billing.serializers import MonthlySaccoInvoiceSerializer
from billing.services import build_invoice_csv, build_invoice_pdf, send_invoice_to_sacco


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
        return Response({'detail': 'Invoice resent successfully.'}, status=status.HTTP_200_OK)


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
            response['Content-Disposition'] = f'attachment; filename=invoice-{invoice.id}.pdf'
            return response

        content = build_invoice_csv(invoice)
        response = HttpResponse(content, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename=invoice-{invoice.id}.csv'
        return response
