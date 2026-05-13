from django.urls import path

from .views import (
    CallbackCreateView,
    MpesaTransactionDetailView,
    MPesaSTKCallbackView,
    STKPushView,
    STKStatusView,
    TransactionDetailView,
    TransactionListView,
)


app_name = 'payments'

urlpatterns = [
    path(
        'transactions/',
        TransactionListView.as_view(),
        name='transaction-list',
    ),
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
    path(
        'mpesa/stk-push/',
        STKPushView.as_view(),
        name='mpesa-stk-push',
    ),
    path(
        'mpesa/stk/<str:checkout_request_id>/status/',
        STKStatusView.as_view(),
        name='mpesa-stk-status',
    ),
    path(
        'callback/mpesa/stk/',
        MPesaSTKCallbackView.as_view(),
        name='mpesa-stk-callback',
    ),
    path('callbacks/', CallbackCreateView.as_view(), name='callback-create'),
]
