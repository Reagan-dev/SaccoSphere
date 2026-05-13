from django.urls import path

from .views import (
    B2CCallbackView,
    B2CDisbursementView,
    B2CHistoryView,
    B2CStatusView,
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
        'mpesa/b2c/disburse/',
        B2CDisbursementView.as_view(),
        name='mpesa-b2c-disburse',
    ),
    path(
        'mpesa/b2c/<str:conversation_id>/status/',
        B2CStatusView.as_view(),
        name='mpesa-b2c-status',
    ),
    path(
        'mpesa/b2c/history/',
        B2CHistoryView.as_view(),
        name='mpesa-b2c-history',
    ),
    path(
        'callback/mpesa/stk/',
        MPesaSTKCallbackView.as_view(),
        name='mpesa-stk-callback',
    ),
    path(
        'callback/mpesa/b2c/',
        B2CCallbackView.as_view(),
        name='mpesa-b2c-callback',
    ),
    path('callbacks/', CallbackCreateView.as_view(), name='callback-create'),
]
