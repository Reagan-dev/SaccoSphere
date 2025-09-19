from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404

from .models import service, saving, loan, insurance
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
    queryset = service.objects.all().order_by("-created_at")
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def create(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return Response(
                {"detail": "Only admins can create services. Users are not allowed to create services."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)


class SavingViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage member savings (deposits/withdrawals).
    - Members can only view and manage their own savings.
    """
    serializer_class = SavingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            saving.objects.filter(member=self.request.user)
            .select_related("service", "member")
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(member=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(
                {"message": "Saving record created successfully.", "data": serializer.data},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoanViewSet(viewsets.ModelViewSet):
    """
    API endpoint to manage member loans.
    - Members can create/view their loans.
    - Admins/staff can see all loans and approve/reject them.
    """
    serializer_class = LoanSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return loan.objects.select_related("service", "member").order_by("-created_at")
        return loan.objects.filter(member=self.request.user).select_related("service").order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(member=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(
                {"message": "Loan application submitted successfully.", "data": serializer.data},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # 🔹 Custom admin actions for loan approval/rejection
    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def approve(self, request, pk=None):
        loan = get_object_or_404(loan, pk=pk)
        if loan.status != "pending":
            return Response({"detail": "Loan is not pending."}, status=status.HTTP_400_BAD_REQUEST)
        loan.status = "approved"
        loan.save()
        return Response({"message": "Loan approved successfully."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def reject(self, request, pk=None):
        loan = get_object_or_404(loan, pk=pk)
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
    serializer_class = InsuranceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            insurance.objects.filter(member=self.request.user)
            .select_related("service", "member")
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(member=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(
                {"message": "Insurance policy created successfully.", "data": serializer.data},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
