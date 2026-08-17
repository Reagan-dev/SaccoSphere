from django.urls import path

from billing.views import (
    CurrentMonthTransactionPreviewView,
    InvoiceDetailView,
    InvoiceDownloadView,
    InvoiceListView,
    InvoiceMarkPaidView,
    MonthlyInvoiceResendView,
    RevenueSummaryView,
)


app_name = 'billing'

urlpatterns = [
    path('invoices/', InvoiceListView.as_view(), name='invoice-list'),
    path(
        'invoices/<uuid:id>/',
        InvoiceDetailView.as_view(),
        name='invoice-detail',
    ),
    path(
        'invoices/<uuid:invoice_id>/resend/',
        MonthlyInvoiceResendView.as_view(),
        name='invoice-resend',
    ),
    path(
        'invoices/<uuid:invoice_id>/download/',
        InvoiceDownloadView.as_view(),
        name='invoice-download',
    ),
    path(
        'invoices/<uuid:invoice_id>/mark-paid/',
        InvoiceMarkPaidView.as_view(),
        name='invoice-mark-paid',
    ),
    path(
        'revenue/summary/',
        RevenueSummaryView.as_view(),
        name='revenue-summary',
    ),
    path(
        'transactions/current-month/',
        CurrentMonthTransactionPreviewView.as_view(),
        name='current-month-transactions',
    ),
]
