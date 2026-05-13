from django.urls import path

from .views import BalanceView, LedgerEntryListView


app_name = 'ledger'

urlpatterns = [
    path('entries/', LedgerEntryListView.as_view(), name='entry-list'),
    path('balance/', BalanceView.as_view(), name='balance'),
]
