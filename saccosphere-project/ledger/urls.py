from django.urls import path

from .views import (
    BalanceView,
    LedgerEntryListView,
    StatementPDFView,
    StatementView,
)


app_name = 'ledger'

urlpatterns = [
    path('entries/', LedgerEntryListView.as_view(), name='entry-list'),
    path('balance/', BalanceView.as_view(), name='balance'),
    path('statement/', StatementView.as_view(), name='statement'),
    path('statement/pdf/', StatementPDFView.as_view(), name='statement-pdf'),
]
