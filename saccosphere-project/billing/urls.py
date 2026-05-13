from django.urls import path

from billing.views import (
    MonthlyInvoiceDetailView,
    MonthlyInvoiceDownloadView,
    MonthlyInvoiceListView,
    MonthlyInvoiceResendView,
)


app_name = 'billing'

urlpatterns = [
    path('invoices/', MonthlyInvoiceListView.as_view(), name='invoice-list'),
    path('invoices/<uuid:id>/', MonthlyInvoiceDetailView.as_view(), name='invoice-detail'),
    path('invoices/<uuid:invoice_id>/resend/', MonthlyInvoiceResendView.as_view(), name='invoice-resend'),
    path('invoices/<uuid:invoice_id>/download/', MonthlyInvoiceDownloadView.as_view(), name='invoice-download'),
]
