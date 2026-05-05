from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated

from config.response import StandardResponseMixin

from .models import MpesaTransaction, Transaction
from .serializers import (
    CallbackSerializer,
    MpesaTransactionSerializer,
    TransactionSerializer,
)


class TransactionListView(StandardResponseMixin, ListAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Transaction.objects.select_related('provider').filter(
            user=self.request.user,
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return self.ok(serializer.data)


class TransactionDetailView(StandardResponseMixin, RetrieveAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Transaction.objects.select_related('provider').filter(
            user=self.request.user,
        )

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return self.ok(serializer.data)


class MpesaTransactionDetailView(StandardResponseMixin, RetrieveAPIView):
    serializer_class = MpesaTransactionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return MpesaTransaction.objects.select_related(
            'transaction',
        ).filter(transaction__user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return self.ok(serializer.data)


class CallbackCreateView(StandardResponseMixin, CreateAPIView):
    serializer_class = CallbackSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        callback = serializer.save()
        data = CallbackSerializer(callback).data
        return self.created(data, 'Callback received')
