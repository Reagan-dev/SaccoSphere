from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
)
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from accounts.models import Sacco, User
from accounts.permissions import IsSaccoAdmin
from notifications.utils import create_notification
from saccomembership.models import Membership

from .engines.guarantor_logic import (
    calculate_guarantee_capacity,
    update_guarantee_capacity,
)
from .engines.loan_limits import calculate_loan_limit
from .models import (
    CRBCheck,
    GuaranteeCapacity,
    Guarantor,
    Loan,
    LoanType,
    RepaymentSchedule,
    Saving,
    SavingsType,
)
from .permissions import GuarantorCapacityCheck
from .serializers import (
    GuarantorSearchResultSerializer,
    GuarantorSerializer,
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


class LoanEligibilityCreateMixin:
    """Create loans only after checking member eligibility limits."""

    def create(self, request, *args, **kwargs):
        """Create a loan application after checking member eligibility."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        loan_type = serializer.validated_data['loan_type']
        amount = serializer.validated_data['amount']
        eligibility = calculate_loan_limit(request.user, loan_type.sacco)

        if not eligibility['eligible']:
            return Response(
                {'reason': eligibility['reason']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if amount > eligibility['max_amount']:
            return Response(
                {
                    'detail': (
                        'Requested amount exceeds your loan limit of '
                        f'KES {eligibility["max_amount"]}.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )


class LoanApplyView(LoanEligibilityCreateMixin, CreateAPIView):
    serializer_class = LoanApplySerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        """Create loan and dispatch guarantor notifications if required."""
        loan = serializer.save()

        # If loan type requires guarantors, move to GUARANTORS_PENDING state
        # and dispatch async task to notify all pending guarantors.
        if loan.loan_type and loan.loan_type.requires_guarantors:
            from .tasks import notify_guarantors_task

            loan.status = Loan.Status.GUARANTORS_PENDING
            loan.save(update_fields=['status', 'updated_at'])

            # Dispatch async task to notify guarantors.
            notify_guarantors_task.delay(str(loan.id))
        else:
            loan.status = Loan.Status.PENDING_APPROVAL
            loan.save(update_fields=['status', 'updated_at'])


class LoanEligibilityView(APIView):
    """Return the authenticated member's loan eligibility for a SACCO."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Calculate and return loan eligibility details."""
        sacco_id = request.query_params.get('sacco_id')

        if not sacco_id:
            return Response(
                {'detail': 'sacco_id parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cache_key = f'loan_eligibility:{request.user.id}:{sacco_id}'
        eligibility = cache.get(cache_key)

        if eligibility is None:
            sacco = get_object_or_404(Sacco, id=sacco_id)
            eligibility = calculate_loan_limit(request.user, sacco)
            cache.set(cache_key, eligibility, timeout=300)

        return Response(eligibility)


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


class LoanCollectionView(LoanEligibilityCreateMixin, ListCreateAPIView):
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


class GuarantorSearchView(APIView):
    """Search for a possible guarantor for a loan."""

    permission_classes = [IsAuthenticated]

    def get(self, request, loan_id):
        """Find a guarantor by phone number or member number."""
        phone = request.query_params.get('phone')
        member_number = request.query_params.get('member_number')

        if not phone and not member_number:
            return Response(
                {'detail': 'phone or member_number query parameter required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        loan = get_object_or_404(
            Loan.objects.select_related(
                'membership',
                'membership__sacco',
                'membership__user',
            ),
            id=loan_id,
            membership__user=request.user,
        )
        guarantor_user = self._find_guarantor_user(
            loan=loan,
            phone=phone,
            member_number=member_number,
        )

        if guarantor_user is None or guarantor_user == request.user:
            return Response(
                {'detail': 'No matching guarantor found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        membership = Membership.objects.filter(
            user=guarantor_user,
            sacco=loan.membership.sacco,
            status=Membership.Status.APPROVED,
        ).first()

        if membership is None:
            return Response(
                {'detail': 'No matching guarantor found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        capacity = self._update_guarantee_capacity(
            guarantor_user,
            loan.membership.sacco,
        )
        data = {
            'user': guarantor_user,
            'member_number': membership.member_number,
            'savings_total': capacity.total_savings,
            'available_capacity': capacity.available_capacity,
            'can_guarantee': capacity.available_capacity > Decimal('0'),
        }
        serializer = GuarantorSearchResultSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _find_guarantor_user(self, loan, phone=None, member_number=None):
        """Find a possible guarantor user by phone or member number."""
        if phone:
            user = User.objects.filter(
                phone_number__icontains=phone,
            ).first()

            if user is not None:
                return user

        if member_number:
            membership = Membership.objects.select_related('user').filter(
                member_number__iexact=member_number,
                sacco=loan.membership.sacco,
                status=Membership.Status.APPROVED,
            ).first()

            if membership is not None:
                return membership.user

        return None

    def _update_guarantee_capacity(self, user, sacco):
        """Refresh and return a user's guarantee capacity."""
        total_savings = Saving.objects.filter(
            membership__user=user,
            membership__sacco=sacco,
            status=Saving.Status.ACTIVE,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        active_guarantees = Guarantor.objects.filter(
            guarantor=user,
            status__in=[
                Guarantor.Status.PENDING,
                Guarantor.Status.APPROVED,
            ],
        ).aggregate(total=Sum('guarantee_amount'))['total'] or Decimal('0')
        available_capacity = max(
            total_savings - active_guarantees,
            Decimal('0'),
        )
        capacity, _ = GuaranteeCapacity.objects.get_or_create(user=user)
        capacity.total_savings = total_savings
        capacity.active_guarantees = active_guarantees
        capacity.available_capacity = available_capacity
        capacity.save(update_fields=[
            'total_savings',
            'active_guarantees',
            'available_capacity',
            'updated_at',
        ])
        return capacity


class GuarantorRequestView(APIView):
    """Request a member to guarantee a loan."""

    permission_classes = [IsAuthenticated]

    def post(self, request, loan_id):
        """Create a pending guarantor request for a loan."""
        loan = get_object_or_404(
            Loan.objects.select_related('membership', 'membership__user'),
            id=loan_id,
            membership__user=request.user,
        )

        if loan.status not in [
            Loan.Status.PENDING,
            Loan.Status.GUARANTORS_PENDING,
        ]:
            return Response(
                {
                    'detail': (
                        'Guarantors can only be requested for pending loans.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        guarantor_user_id = request.data.get('guarantor_user_id')
        guarantee_amount = self._parse_guarantee_amount(
            request.data.get('guarantee_amount'),
        )

        if not guarantor_user_id or guarantee_amount is None:
            return Response(
                {
                    'detail': (
                        'guarantor_user_id and guarantee_amount are required.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if guarantee_amount <= Decimal('0'):
            return Response(
                {'detail': 'Guarantee amount must be greater than zero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if guarantee_amount > loan.amount:
            return Response(
                {'detail': 'Guarantee amount cannot exceed loan amount.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        guarantor_user = get_object_or_404(User, id=guarantor_user_id)

        if guarantor_user == request.user:
            return Response(
                {'detail': 'You cannot guarantee your own loan.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        capacity_data = calculate_guarantee_capacity(guarantor_user)
        if capacity_data['available_capacity'] < guarantee_amount:
            return Response(
                {'detail': 'Guarantor has insufficient capacity.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            if Guarantor.objects.filter(
                loan=loan,
                guarantor=guarantor_user,
            ).exists():
                return Response(
                    {'detail': 'Guarantor request already exists.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            guarantor = Guarantor.objects.create(
                loan=loan,
                guarantor=guarantor_user,
                guarantee_amount=guarantee_amount,
                status=Guarantor.Status.PENDING,
            )

            if loan.status != Loan.Status.GUARANTORS_PENDING:
                loan.status = Loan.Status.GUARANTORS_PENDING
                loan.save(update_fields=['status', 'updated_at'])

        serializer = GuarantorSerializer(guarantor)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _parse_guarantee_amount(self, value):
        """Parse a guarantee amount into Decimal."""
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None


class GuarantorRespondView(APIView):
    """Guarantor approval or decline of a guarantee request."""

    permission_classes = [IsAuthenticated, GuarantorCapacityCheck]

    def post(self, request, loan_id, guarantor_id):
        """
        Record guarantor approval or decline.

        APPROVE: Validate capacity, update status, check if all approved,
        transition loan to BOARD_REVIEW if yes, otherwise remain
        GUARANTORS_PENDING.

        DECLINE: Update status to DECLINED, reset loan to PENDING,
        notify applicant.

        Returns:
            Response: 200 OK with updated guarantor data on success.
        """
        guarantor = get_object_or_404(
            Guarantor.objects.select_related(
                'loan',
                'loan__membership__user',
                'guarantor',
            ),
            id=guarantor_id,
            loan_id=loan_id,
        )

        # Verify request user is the guarantor.
        if guarantor.guarantor != request.user:
            return Response(
                {'detail': 'You are not this guarantor.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Parse action and optional notes.
        action = request.data.get('action', '').upper()
        notes = request.data.get('notes', '')

        if action not in ['APPROVE', 'DECLINE']:
            return Response(
                {'detail': 'action must be APPROVE or DECLINE.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        loan = guarantor.loan

        # Verify guarantor status is still PENDING.
        if guarantor.status != Guarantor.Status.PENDING:
            return Response(
                {
                    'detail': (
                        f'Guarantor status is already {guarantor.status}.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            if action == 'APPROVE':
                # Validate guarantor has sufficient capacity.
                capacity = GuaranteeCapacity.objects.get(
                    user=request.user,
                )

                if capacity.available_capacity < guarantor.guarantee_amount:
                    return Response(
                        {
                            'detail': (
                                'Insufficient guarantee capacity for this '
                                'amount.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Update guarantor status and timestamp.
                guarantor.status = Guarantor.Status.APPROVED
                guarantor.responded_at = timezone.now()
                if notes:
                    guarantor.notes = notes
                guarantor.save()

                # Recalculate and update guarantor's capacity.
                update_guarantee_capacity(request.user)

                # Check if all required guarantors are now APPROVED.
                total_required = (
                    loan.loan_type.min_guarantors
                    if loan.loan_type
                    else 0
                )
                approved_count = loan.guarantors.filter(
                    status=Guarantor.Status.APPROVED,
                ).count()

                # If all required guarantors approved, move to PENDING_APPROVAL.
                if approved_count >= total_required and total_required > 0:
                    loan.status = Loan.Status.PENDING_APPROVAL
                    loan.save(update_fields=['status', 'updated_at'])

                    # Notify SACCO admin that loan is ready for review.
                    self._notify_sacco_admin_board_review(loan)

            elif action == 'DECLINE':
                # Update guarantor status and timestamp.
                guarantor.status = Guarantor.Status.DECLINED
                guarantor.responded_at = timezone.now()
                if notes:
                    guarantor.notes = notes
                guarantor.save()

                # Reset loan status back to PENDING for resubmission.
                loan.status = Loan.Status.PENDING
                loan.save(update_fields=['status', 'updated_at'])

                # Notify applicant that a guarantor declined.
                self._notify_applicant_guarantor_declined(
                    loan,
                    guarantor,
                )

        serializer = GuarantorSerializer(guarantor)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _notify_sacco_admin_board_review(self, loan):
        """Notify SACCO admin that loan is ready for board review."""
        # Get all SACCO_ADMIN users for this loan's SACCO.
        from saccomanagement.models import Role

        admin_role = Role.objects.filter(
            name='SACCO_ADMIN',
            sacco=loan.membership.sacco,
        ).first()

        if admin_role:
            for user in admin_role.users.all():
                applicant_name = (
                    f'{loan.membership.user.first_name} '
                    f'{loan.membership.user.last_name}'
                )
                create_notification(
                    user=user,
                    title='Loan Ready for Board Review',
                    message=(
                        f'Loan of KES {loan.amount:.2f} from {applicant_name} '
                        f'has all guarantor approvals and is ready for board '
                        f'review.'
                    ),
                    category='LOAN',
                    action_url=f'/loans/{loan.id}/',
                    dispatch_async=False,
                )

    def _notify_applicant_guarantor_declined(self, loan, guarantor):
        """Notify loan applicant that a guarantor declined."""
        guarantor_name = (
            f'{guarantor.guarantor.first_name} '
            f'{guarantor.guarantor.last_name}'
        )
        create_notification(
            user=loan.membership.user,
            title='Guarantor Request Declined',
            message=(
                f'{guarantor_name} declined to guarantee your loan. '
                f'Your loan request has been reset to pending. '
                f'Please request another guarantor.'
            ),
            category='LOAN',
            action_url=f'/loans/{loan.id}/',
            dispatch_async=False,
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


class CRBCheckView(APIView):
    """
    Perform CRB check for a loan application.
    
    POST /api/v1/management/loans/<pk>/crb-check/
    
    Checks credit status via Metropol CRB. Caches results for 30 days
    unless force_refresh=true is passed.
    """
    
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    
    def post(self, request, pk):
        """Perform CRB check for the specified loan."""
        loan = get_object_or_404(
            Loan.objects.select_related('membership', 'membership__user'),
            id=pk,
        )
        
        # Verify user is admin for this loan's SACCO
        from saccomanagement.models import Role
        if not request.user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco=loan.membership.sacco,
        ).exists():
            return Response(
                {'detail': 'You are not an admin for this SACCO.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        # Get member's ID number from KYC
        from accounts.models import KYCVerification
        kyc = KYCVerification.objects.filter(
            user=loan.membership.user,
        ).first()
        
        if not kyc or not kyc.id_number:
            return Response(
                {'detail': 'Member KYC verification with ID number required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        id_number = kyc.id_number
        phone_number = loan.membership.user.phone_number
        
        # Check for existing recent CRB check (within 30 days)
        force_refresh = request.query_params.get('force_refresh', 'false').lower() == 'true'
        
        if not force_refresh:
            cutoff_date = timezone.now() - timedelta(days=30)
            existing_check = loan.crb_checks.filter(
                checked_at__gte=cutoff_date,
            ).order_by('-checked_at').first()
            
            if existing_check:
                return Response({
                    'id': str(existing_check.id),
                    'score': existing_check.score,
                    'band': existing_check.band,
                    'listed_negative': existing_check.listed_negative,
                    'provider': existing_check.provider,
                    'reference': existing_check.reference,
                    'checked_at': existing_check.checked_at.isoformat(),
                    'cached': True,
                })
        
        # Perform new CRB check
        from .integrations.metropol_client import MetropolClient, CRBCheckError
        
        try:
            client = MetropolClient()
            crb_result = client.check_credit(id_number, phone_number)
        except CRBCheckError as exc:
            return Response(
                {'detail': f'CRB check failed: {str(exc)}'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        
        # Create CRBCheck record
        crb_check = CRBCheck.objects.create(
            loan=loan,
            score=crb_result.get('score'),
            band=crb_result.get('band'),
            listed_negative=crb_result.get('listed_negative', False),
            provider=crb_result.get('provider', 'metropol'),
            reference=crb_result.get('reference'),
            raw_response=crb_result,
            checked_by=request.user,
        )
        
        return Response({
            'id': str(crb_check.id),
            'score': crb_check.score,
            'band': crb_check.band,
            'listed_negative': crb_check.listed_negative,
            'provider': crb_check.provider,
            'reference': crb_check.reference,
            'checked_at': crb_check.checked_at.isoformat(),
            'cached': False,
        }, status=status.HTTP_201_CREATED)


