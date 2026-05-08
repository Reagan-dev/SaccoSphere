from decimal import Decimal
from django.db.models import Sum
from rest_framework.views import APIView
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
)
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from .models import Loan, LoanType, RepaymentSchedule, Saving, SavingsType
from .serializers import (
    LoanApplySerializer,
    LoanDetailSerializer,
    LoanListSerializer,
    LoanTypeSerializer,
    RepaymentScheduleSerializer,
    SavingSerializer,
    SavingsTypeSerializer,
)


class SavingsTypeViewSet(ModelViewSet):
    serializer_class = SavingsTypeSerializer
    queryset = SavingsType.objects.select_related('sacco')

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsAdminUser()]

    def get_queryset(self):
        queryset = super().get_queryset()
        sacco = self.request.query_params.get('sacco')
        sacco_id = self.request.query_params.get('sacco_id')

        if sacco:
            queryset = queryset.filter(sacco_id=sacco)
        if sacco_id:
            queryset = queryset.filter(sacco_id=sacco_id)

        return queryset


class SavingListView(ListAPIView):
    serializer_class = SavingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Saving.objects.filter(
            membership__user=self.request.user,
        ).select_related(
            'membership__sacco',
            'savings_type',
        )
        sacco = self.request.query_params.get('sacco')

        if sacco:
            queryset = queryset.filter(membership__sacco_id=sacco)

        return queryset


class LoanTypeListView(ListAPIView):
    serializer_class = LoanTypeSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = LoanType.objects.filter(is_active=True).select_related(
            'sacco',
        )
        sacco_id = self.request.query_params.get('sacco_id')

        if sacco_id:
            queryset = queryset.filter(sacco_id=sacco_id)

        return queryset


class LoanApplyView(CreateAPIView):
    serializer_class = LoanApplySerializer
    permission_classes = [IsAuthenticated]


class LoanListView(ListAPIView):
    serializer_class = LoanListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Loan.objects.filter(
            membership__user=self.request.user,
        ).select_related(
            'membership__sacco',
            'loan_type',
        )
        status = self.request.query_params.get('status')
        sacco = self.request.query_params.get('sacco')

        if status:
            queryset = queryset.filter(status=status)
        if sacco:
            queryset = queryset.filter(membership__sacco_id=sacco)

        return queryset


class LoanCollectionView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return LoanApplySerializer
        return LoanListSerializer

    def get_queryset(self):
        queryset = Loan.objects.filter(
            membership__user=self.request.user,
        ).select_related(
            'membership__sacco',
            'loan_type',
        )
        status = self.request.query_params.get('status')
        sacco = self.request.query_params.get('sacco')

        if status:
            queryset = queryset.filter(status=status)
        if sacco:
            queryset = queryset.filter(membership__sacco_id=sacco)

        return queryset


class LoanDetailView(RetrieveAPIView):
    serializer_class = LoanDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Loan.objects.filter(
            membership__user=self.request.user,
        ).select_related(
            'membership__sacco',
            'loan_type',
        )


class SavingsBreakdownView(APIView):
    """
    Get savings breakdown by type for a specific SACCO.
    
    GET /api/v1/services/savings/breakdown/?sacco_id=
    
    Returns aggregated totals for BOSA, FOSA, and SHARE_CAPITAL.
    """
    
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Calculate and return savings breakdown."""
        sacco_id = request.query_params.get('sacco_id')
        
        if not sacco_id:
            return Response({
                'success': False,
                'message': 'sacco_id parameter is required.',
                'error_code': 'MISSING_PARAMETER',
            }, status=400)

        # Get aggregated savings data
        savings_data = Saving.objects.filter(
            membership__user=request.user,
            membership__sacco_id=sacco_id,
            status='ACTIVE'
        ).values('savings_type__name').annotate(
            total=Sum('amount')
        )

        # Initialize breakdown with defaults
        breakdown = {
            'sacco_id': sacco_id,
            'sacco_name': '',
            'bosa_total': Decimal('0.00'),
            'fosa_total': Decimal('0.00'),
            'share_capital_total': Decimal('0.00'),
            'dividend_eligible_total': Decimal('0.00'),
            'total': Decimal('0.00'),
        }

        # Get SACCO name
        from accounts.models import Sacco
        try:
            sacco = Sacco.objects.get(id=sacco_id)
            breakdown['sacco_name'] = sacco.name
        except Sacco.DoesNotExist:
            pass

        # Process aggregated data
        for item in savings_data:
            savings_type = item['savings_type__name']
            total = item['total'] or Decimal('0.00')
            
            if savings_type == SavingsType.Name.BOSA:
                breakdown['bosa_total'] = total
            elif savings_type == SavingsType.Name.FOSA:
                breakdown['fosa_total'] = total
            elif savings_type == SavingsType.Name.SHARE_CAPITAL:
                breakdown['share_capital_total'] = total

        # Calculate total and dividend eligible amount
        breakdown['total'] = (
            breakdown['bosa_total'] + 
            breakdown['fosa_total'] + 
            breakdown['share_capital_total']
        )

        # Get dividend eligible total
        dividend_eligible = Saving.objects.filter(
            membership__user=request.user,
            membership__sacco_id=sacco_id,
            status='ACTIVE',
            dividend_eligible=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        breakdown['dividend_eligible_total'] = dividend_eligible

        return Response({
            'success': True,
            'data': breakdown
        })


class RepaymentScheduleView(ListAPIView):
    serializer_class = RepaymentScheduleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Get or generate repayment schedule for the loan."""
        from django.db import transaction
        from django.utils import timezone
        from .engines.amortization import generate_repayment_schedule
        
        loan_id = self.kwargs['id']
        
        # Check if schedule already exists
        existing_schedule = RepaymentSchedule.objects.filter(
            loan__id=loan_id,
            loan__membership__user=self.request.user,
        ).select_related('loan')
        
        if existing_schedule.exists():
            return existing_schedule
        
        # Get the loan
        try:
            loan = Loan.objects.get(
                id=loan_id,
                membership__user=self.request.user,
            )
        except Loan.DoesNotExist:
            return RepaymentSchedule.objects.none()
        
        # Generate schedule if loan is in appropriate status
        if loan.status not in [Loan.Status.APPROVED, Loan.Status.ACTIVE, Loan.Status.DISBURSEMENT_PENDING]:
            return RepaymentSchedule.objects.none()
        
        # Use disbursement date or today as start date
        start_date = loan.disbursement_date or timezone.localdate()
        
        # Generate amortisation schedule
        schedule_data = generate_repayment_schedule(
            loan_amount=loan.amount,
            annual_interest_rate=loan.interest_rate,
            term_months=loan.term_months,
            start_date=start_date,
        )
        
        # Create RepaymentSchedule records in a transaction
        with transaction.atomic():
            schedule_instances = []
            for instalment in schedule_data:
                schedule_instances.append(
                    RepaymentSchedule(
                        loan=loan,
                        instalment_number=instalment['instalment_number'],
                        due_date=instalment['due_date'],
                        amount=instalment['amount'],
                        principal=instalment['principal'],
                        interest=instalment['interest'],
                        balance_after=instalment['balance_after'],
                    )
                )
            
            RepaymentSchedule.objects.bulk_create(schedule_instances)
        
        # Return the newly created schedule
        return RepaymentSchedule.objects.filter(
            loan__id=loan_id,
            loan__membership__user=self.request.user,
        ).select_related('loan')
