from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAdminUser

from .models import Service, Saving, Loan, Insurance
from .serializers import (
    ServiceSerializer,
    SavingSerializer,
    LoanSerializer,
    InsuranceSerializer,
)


class ServiceViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage Sacco services (Savings, Loans, Insurance).
    - Anyone can view services.
    - Only staff/admin users can create, update, or delete.
    """
    queryset = Service.objects.all().order_by("-created_at")
    serializer_class = ServiceSerializer

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAdminUser()]
        return [permissions.AllowAny()]  # Anyone can view/list


class SavingViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage member savings (deposits/withdrawals).
    - Members can only view and manage their own savings.
    """
    queryset = Saving.objects.all()
    serializer_class = SavingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Saving.objects.filter(member=self.request.user)
            .select_related("service", "member")
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(member=self.request.user)


class LoanViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage member loans.
    - Members can create/view their loans.
    - Admins/staff can see all loans and approve/reject them.
    """
    queryset = Loan.objects.all()
    serializer_class = LoanSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:  # Admin sees all
            return Loan.objects.all().select_related("service", "member").order_by("-created_at")
        return Loan.objects.filter(member=self.request.user).select_related("service", "member").order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(member=self.request.user)

    # 🔹 Custom admin actions for loan approval/rejection
    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def approve(self, request, pk=None):
        loan = get_object_or_404(Loan, pk=pk)
        if loan.status != "pending":
            return Response({"detail": "Loan is not pending."}, status=status.HTTP_400_BAD_REQUEST)
        loan.status = "approved"
        loan.save()
        return Response({"message": "Loan approved successfully."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def reject(self, request, pk=None):
        loan = get_object_or_404(Loan, pk=pk)
        if loan.status != "pending":
            return Response({"detail": "Loan is not pending."}, status=status.HTTP_400_BAD_REQUEST)
        loan.status = "rejected"
        loan.save()
        return Response({"message": "You do not qualify for this loan."}, status=status.HTTP_200_OK)


class InsuranceViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage member insurance policies.
    - Members can only view/manage their own policies.
    """
    queryset = Insurance.objects.all()
    serializer_class = InsuranceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Insurance.objects.filter(member=self.request.user)
            .select_related("service", "member")
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(member=self.request.user)