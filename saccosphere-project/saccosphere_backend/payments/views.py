# payments/views.py
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404

from .models import PaymentProvider, Transaction, Callback
from .serializers import PaymentProviderSerializer, TransactionSerializer, CallbackSerializer


class PaymentProviderViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage payment providers (e.g. M-Pesa, Airtel, Bank).
    - Only admins can create/update/delete.
    - Anyone authenticated can view active providers.
    """
    queryset = PaymentProvider.objects.all().order_by("-created_at")
    serializer_class = PaymentProviderSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [permissions.IsAuthenticated()]  # members can see available providers
        return [permissions.IsAdminUser()]  # only admins can manage

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            provider = serializer.save()
            return Response(
                {"message": "Payment provider created successfully.", "data": PaymentProviderSerializer(provider).data},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TransactionViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage payment transactions.
    - Members can create transactions & view their own.
    - Admins can view all transactions.
    """
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return Transaction.objects.select_related("provider", "user").order_by("-created_at")
        return Transaction.objects.filter(user=self.request.user).select_related("provider").order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            transaction = serializer.save(user=request.user)
            return Response(
                {"message": "Transaction created successfully.", "data": TransactionSerializer(transaction).data},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def mark_success(self, request, pk=None):
        """
        Admin action: Mark a transaction as SUCCESS.
        """
        transaction = get_object_or_404(Transaction, pk=pk)
        transaction.status = "SUCCESS"
        transaction.save()
        return Response({"message": "Successful."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def mark_failed(self, request, pk=None):
        """
        Admin action: Mark a transaction as FAILED.
        """
        transaction = get_object_or_404(Transaction, pk=pk)
        transaction.status = "FAILED"
        transaction.save()
        return Response({"message": "Transaction FAILED."}, status=status.HTTP_200_OK)


class CallbackViewSet(viewsets.ModelViewSet):
    """
    API endpoint to store raw callbacks from payment providers.
    - Providers post callback payloads.
    - Admins can view/manage.
    """
    queryset = Callback.objects.all().select_related("transaction", "provider").order_by("-received_at")
    serializer_class = CallbackSerializer

    def get_permissions(self):
        if self.action in ["create"]:
            return [permissions.AllowAny()]  # Providers can hit callback without auth
        return [permissions.IsAdminUser()]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            callback = serializer.save()
            return Response(
                {"message": "Callback received successfully.", "data": CallbackSerializer(callback).data},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def mark_processed(self, request, pk=None):
        """
        Admin action: Mark a callback as processed.
        """
        callback = get_object_or_404(Callback, pk=pk)
        callback.processed = True
        callback.save()
        return Response({"message": "Callback marked as processed."}, status=status.HTTP_200_OK)
