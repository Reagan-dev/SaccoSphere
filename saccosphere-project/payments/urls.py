from django.urls import path

from .views import (
    CallbackCreateView,
    MpesaTransactionDetailView,
    TransactionDetailView,
    TransactionListView,
)


app_name = 'payments'

urlpatterns = [
    path('transactions/', TransactionListView.as_view(), name='transaction-list'),
    path(
        'transactions/<uuid:id>/',
        TransactionDetailView.as_view(),
        name='transaction-detail',
    ),
    path(
        'mpesa/<uuid:id>/',
        MpesaTransactionDetailView.as_view(),
        name='mpesa-detail',
    ),
    path('callbacks/', CallbackCreateView.as_view(), name='callback-create'),
]
