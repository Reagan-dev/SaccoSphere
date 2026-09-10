from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
    RetrieveUpdateAPIView,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from accounts.models import Sacco, SaccoSettings, User
from accounts.permissions import (
    IsSaccoAdmin,
    IsSaccoAdminOrSuperAdmin,
    IsSuperAdmin,
)
from billing.serializers import DisbursementAuditSerializer
from notifications.utils import create_notification
from saccomembership.models import Membership
from saccomanagement.audit_logger import log_audit
from saccomanagement.mixins import SaccoScopedMixin
from saccomanagement.models import Role
from saccomanagement.odpc_logging import (
    ConsentLogWriteError,
    create_data_consent_log,
)
from guarantor.utils import check_loan_guarantors_complete

from .engines.guarantor_logic import (
    calculate_guarantee_capacity,
    lock_guarantee_capacity,
    update_guarantee_capacity,
)
from .engines.loan_limits import (
    calculate_loan_limit,
    lock_member_loan_capacity_rows,
)
from .engines.liquidity_monitor import check_liquidity_risk
from .models import (
    CRBCheck,
    DisbursementAuditLog,
    DividendDeclaration,
    DividendPayout,
    Guarantor,
    LiquidityAlert,
    Loan,
    LoanType,
    NPLFlag,
    RepaymentSchedule,
    Saving,
    SavingsType,
)
from .serializers import (
    DividendDeclarationSerializer,
    DividendPayoutSerializer,
    GuarantorSearchResultSerializer,
    GuarantorSerializer,
    LoanApplySerializer,
    LoanDetailSerializer,
    LoanListSerializer,
    LoanTypeSerializer,
    RepaymentScheduleSerializer,
    SavingSerializer,
    SavingsTypePublicSerializer,
    SavingsTypeSerializer,
    SavingsTypeWriteSerializer,
)
from .tasks import (
    _notify_sacco_admins,
    _notify_superadmins,
    _record_disbursement_invoice_item,
)


class SavingsTypeViewSet(SaccoScopedMixin, ModelViewSet):
    """Savings-product CRUD.

    Writes: authenticated SACCO admins, scoped to their own SACCO.

    Reads (list/retrieve): authenticated users only - the old
    ``AllowAny`` + ``fields='__all__'`` + optional filter let anyone
    enumerate every SACCO's ``interest_rate`` / ``minimum_contribution``
    / ``is_active`` / internal ids. ``list`` now *requires* a ``sacco``
    (or ``sacco_id``) query param so it is always scoped to exactly one
    tenant, and non-admins get the narrow
    :class:`SavingsTypePublicSerializer` (no ids, no ops flags). A SACCO
    admin listing their own SACCO still gets the full serializer for the
    management UI.
    """

    serializer_class = SavingsTypeSerializer
    queryset = SavingsType.objects.select_related('sacco')
    _READ_ACTIONS = ('list', 'retrieve')
    _WRITE_ACTIONS = ('create', 'update', 'partial_update', 'destroy')

    def get_permissions(self):
        if self.action in self._READ_ACTIONS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsSaccoAdmin()]

    def should_enforce_sacco_scope(self):
        # Reads are not routed through the admin-only scoping context
        # (they must work for ordinary members too); they enforce a
        # required single-SACCO filter directly instead.
        return self.action not in self._READ_ACTIONS

    def get_serializer_class(self):
        if self.action in self._WRITE_ACTIONS:
            return SavingsTypeWriteSerializer
        if self.action == 'list' and self._caller_administers_requested_sacco():
            return SavingsTypeSerializer
        return SavingsTypePublicSerializer

    def _requested_sacco_id(self):
        return (
            self.request.query_params.get('sacco')
            or self.request.query_params.get('sacco_id')
        )

    def _caller_administers_requested_sacco(self):
        """True if the caller is an admin of the SACCO being listed."""
        user = self.request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or user.roles.filter(
            name=Role.SUPER_ADMIN, is_active=True,
        ).exists():
            return True
        sacco_id = self._requested_sacco_id()
        return bool(sacco_id) and user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco_id=sacco_id,
            is_active=True,
        ).exists()

    def list(self, request, *args, **kwargs):
        if not self._requested_sacco_id():
            return Response(
                {
                    'detail': (
                        'A sacco query parameter is required to list '
                        'savings types.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()

        # Writes: strict SACCO scoping from the request context.
        if self.action in self._WRITE_ACTIONS:
            return self.apply_sacco_scope(queryset)

        # Reads: never global - scoped to exactly one SACCO. ``list``
        # has already 400'd if the param is missing; ``retrieve`` fetches
        # a single object by pk (one tenant) so an absent param is fine.
        sacco_id = self._requested_sacco_id()
        if sacco_id:
            queryset = queryset.filter(sacco_id=sacco_id)

        return queryset

    def get_serializer(self, *args, **kwargs):
        if self.action in ['create', 'update', 'partial_update']:
            data = kwargs.get('data')
            if data is not None:
                mutable_data = data.copy()
                mutable_data.pop('sacco', None)
                mutable_data.pop('sacco_id', None)
                kwargs['data'] = mutable_data
        return super().get_serializer(*args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(sacco=self.get_sacco_context())

    def perform_update(self, serializer):
        serializer.save(sacco=self.get_sacco_context())

    def destroy(self, request, *args, **kwargs):
        """Refuse to hard-delete a savings type that still has accounts.

        ``Saving.savings_type`` is ``on_delete=SET_NULL``, so deleting a
        type in use would silently NULL it on every affected account,
        dropping those balances out of the savings breakdown and the
        dividend calculator (which filters by ``savings_type``). Retiring
        a product is ``PATCH is_active=false`` instead.
        """
        instance = self.get_object()
        # ``instance`` is already SACCO-scoped by get_queryset(); a
        # Saving's savings_type belongs to exactly one SACCO, so this
        # count is inherently tenant-bound.
        in_use = Saving.objects.filter(savings_type=instance).count()
        if in_use:
            return Response(
                {
                    'detail': (
                        f'Cannot delete this savings type: {in_use} '
                        f'savings account(s) still reference it. Retire '
                        f'it instead by patching is_active=false.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        response = super().destroy(request, *args, **kwargs)
        log_audit(
            request.user,
            'DELETE',
            'SavingsType',
            instance.id,
            old_values={
                'name': instance.name,
                'sacco_id': str(instance.sacco_id),
            },
            request=request,
        )
        return response


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


class SavingsAccountAdminOpenView(SaccoScopedMixin, APIView):
    """Admin-initiated opening of a member savings account.

    POST /savings/admin/  {membership_id, savings_type_id, opening_balance?}

    The only product path that creates a ``Saving``; it delegates to
    ``services.engines.savings_provisioning.open_savings_account`` so the
    tenant checks and the opening-deposit ledger entry are not
    re-implemented here.
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    # Opens an account and posts an opening ledger entry - a multi-SACCO
    # admin must name the tenant explicitly.
    require_sacco_header = True

    def post(self, request):
        response = self._set_sacco_context()
        if response:
            return response

        from .engines.savings_provisioning import (
            SavingsAccountError,
            open_savings_account,
        )
        from .serializers import OpenSavingsAccountSerializer

        sacco = self.get_sacco_context()
        serializer = OpenSavingsAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        # Both lookups are scoped to the admin's SACCO: a membership or
        # savings type from another tenant 404s here, so a cross-tenant
        # pairing can never reach open_savings_account().
        membership = get_object_or_404(
            Membership.objects.select_related('sacco', 'user'),
            id=payload['membership_id'],
            sacco=sacco,
        )
        savings_type = get_object_or_404(
            SavingsType.objects.filter(sacco=sacco),
            id=payload['savings_type_id'],
        )

        try:
            saving = open_savings_account(
                membership=membership,
                savings_type=savings_type,
                opening_balance=payload.get('opening_balance'),
            )
        except SavingsAccountError as exc:
            detail = exc.messages[0] if exc.messages else str(exc)
            return Response(
                {'detail': detail},
                status=status.HTTP_409_CONFLICT,
            )

        opening_balance = payload.get('opening_balance') or Decimal('0.00')
        log_audit(
            request.user,
            'SAVINGS_ACCOUNT_OPENED',
            'Saving',
            saving.id,
            new_values={
                'membership_id': str(membership.id),
                'savings_type_id': str(savings_type.id),
                'sacco_id': str(sacco.id),
                'opening_balance': str(opening_balance),
            },
            request=request,
        )
        return Response(
            SavingSerializer(saving).data,
            status=status.HTTP_201_CREATED,
        )


class _SavingAdminActionView(SaccoScopedMixin, APIView):
    """Shared base: SACCO-scoped admin action on one member's saving.

    Resolves the saving inside the admin's SACCO (a cross-tenant id
    404s); subclasses do the actual change through the audited
    ``services.engines.savings_admin_ops`` helpers.
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    # A money-affecting admin write - a multi-SACCO admin must name the
    # tenant with X-Sacco-ID.
    require_sacco_header = True

    def _get_scoped_saving(self, sacco, saving_id):
        return get_object_or_404(
            Saving.objects.select_related(
                'membership',
                'membership__user',
                'membership__sacco',
                'savings_type',
            ),
            id=saving_id,
            membership__sacco=sacco,
        )


class SavingsStatusActionView(_SavingAdminActionView):
    """POST /savings/<id>/status/ {action: freeze|close|reactivate, reason}."""

    def post(self, request, id=None):
        response = self._set_sacco_context()
        if response:
            return response

        from .engines.savings_admin_ops import (
            SavingsAdminOpError,
            apply_savings_status_action,
        )
        from .serializers import SavingsStatusActionSerializer

        serializer = SavingsStatusActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        saving = self._get_scoped_saving(self.get_sacco_context(), id)
        try:
            saving = apply_savings_status_action(
                saving,
                action=data['action'],
                actor=request.user,
                reason=data['reason'],
                request=request,
            )
        except SavingsAdminOpError as exc:
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(SavingSerializer(saving).data)


class SavingsDividendEligibilityView(_SavingAdminActionView):
    """POST /savings/<id>/dividend-eligibility/ {eligible: bool, reason}."""

    def post(self, request, id=None):
        response = self._set_sacco_context()
        if response:
            return response

        from .engines.savings_admin_ops import (
            SavingsAdminOpError,
            set_dividend_eligibility,
        )
        from .serializers import SavingsDividendEligibilitySerializer

        serializer = SavingsDividendEligibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        saving = self._get_scoped_saving(self.get_sacco_context(), id)
        try:
            saving = set_dividend_eligibility(
                saving,
                eligible=data['eligible'],
                actor=request.user,
                reason=data['reason'],
                request=request,
            )
        except SavingsAdminOpError as exc:
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(SavingSerializer(saving).data)


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
        """Create a loan application after checking member eligibility.

        The eligibility read and the loan insert run in one transaction
        with the member's savings and outstanding-loan rows locked
        FOR UPDATE first (see lock_member_loan_capacity_rows). Two
        concurrent applications from the same member therefore serialise -
        the second re-reads a limit that already counts the first loan -
        so they cannot jointly exceed the limit off a stale snapshot.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        loan_type = serializer.validated_data['loan_type']
        amount = serializer.validated_data['amount']

        with transaction.atomic():
            membership = Membership.objects.filter(
                user=request.user,
                sacco=loan_type.sacco,
                status=Membership.Status.APPROVED,
            ).first()
            if membership is not None:
                lock_member_loan_capacity_rows(membership)

            eligibility = calculate_loan_limit(request.user, loan_type.sacco)

            if not eligibility['eligible']:
                return Response(
                    {'reason': eligibility['reason']},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # min_loan_amount gates the request size only. It must never
            # raise the computed eligibility limit itself, which would let
            # a member borrow more than their savings-based eligibility.
            sacco_settings = getattr(loan_type.sacco, 'settings', None)
            if (
                sacco_settings is not None
                and amount < sacco_settings.min_loan_amount
            ):
                return Response(
                    {
                        'detail': (
                            'Requested amount is below the minimum loan '
                            f'amount of KES {sacco_settings.min_loan_amount} '
                            'for this SACCO.'
                        ),
                    },
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

            # perform_create now runs inside the mixin's eligibility
            # transaction, so defer the enqueue until that commits: never
            # notify guarantors for a loan row that then rolls back, and
            # don't fail the application if the broker is briefly down.
            loan_id = str(loan.id)
            transaction.on_commit(
                lambda: notify_guarantors_task.delay(loan_id),
            )
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


class LoanDisbursementDisputeListView(APIView):
    """List loans whose disbursement needs super-admin attention."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        loans = Loan.objects.select_related(
            'membership__sacco',
            'membership__user',
        ).filter(
            disbursement_status__in=[
                Loan.DisbursementStatus.DISPUTED,
                Loan.DisbursementStatus.UNDER_REVIEW,
            ],
        ).annotate(
            audit_log_count=Count('disbursementauditlog'),
        ).order_by('-member_disputed_at', '-created_at')

        data = [
            {
                'loan_id': str(loan.id),
                'sacco': loan.membership.sacco.name,
                'member': f"{loan.membership.user.first_name} {loan.membership.user.last_name}",
                'amount': str(loan.amount),
                'status': loan.disbursement_status,
                'disputed_at': loan.member_disputed_at.isoformat() if loan.member_disputed_at else None,
                'dispute_reason': loan.dispute_reason or '',
                'mpesa_conversation_id': loan.mpesa_conversation_id or '',
                'audit_log_count': loan.audit_log_count,
            }
            for loan in loans
        ]
        return Response(data)


class ConfirmDisbursementView(APIView):
    """Confirm that a member received a disbursed loan.

    POST only: this mutates disbursement state, so it must not be
    reachable by a plain GET that link-prefetch bots (mail scanners,
    chat unfurlers, antivirus) fire automatically. Authenticity comes
    from the signed ``token`` in the request body, same as the
    password-reset confirm flow; with no SessionAuthentication there is
    no CSRF token to supply.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        loan = self._get_loan_from_token(request)
        if isinstance(loan, Response):
            return loan

        with transaction.atomic():
            loan = (
                Loan.objects.select_for_update()
                .select_related('membership', 'membership__sacco')
                .get(id=loan.id)
            )
            if loan.disbursement_status == Loan.DisbursementStatus.DISPUTED:
                return Response(
                    {'detail': 'This disbursement is already disputed.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if loan.disbursement_status in [
                Loan.DisbursementStatus.MEMBER_CONFIRMED,
                Loan.DisbursementStatus.AUTO_CONFIRMED,
            ]:
                return Response(
                    {
                        'status': 'confirmed',
                        'message': 'Thank you for confirming',
                    },
                    status=status.HTTP_200_OK,
                )

            loan.disbursement_status = Loan.DisbursementStatus.MEMBER_CONFIRMED
            loan.member_confirmed_at = timezone.now()
            loan.save(
                update_fields=[
                    'disbursement_status',
                    'member_confirmed_at',
                    'updated_at',
                ],
            )

            DisbursementAuditLog.objects.create(
                loan=loan,
                event='MEMBER_CONFIRMED',
                actor=None,
                actor_role='member',
                ip_address=self._get_ip(request),
                details={
                    'confirmed_at': loan.member_confirmed_at.isoformat(),
                },
            )

            _record_disbursement_invoice_item(loan)

        return Response(
            {
                'status': 'confirmed',
                'message': 'Thank you for confirming',
            },
            status=status.HTTP_200_OK,
        )

    def _get_loan_from_token(self, request):
        token = request.data.get('token')
        if not token:
            return Response(
                {'detail': 'token is required in the request body.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        signer = TimestampSigner()
        try:
            loan_id = signer.unsign(token, max_age=86400)
        except SignatureExpired:
            return Response(
                {'detail': 'Disbursement confirmation link has expired.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BadSignature:
            return Response(
                {'detail': 'Invalid disbursement confirmation token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return get_object_or_404(Loan, id=loan_id)

    def _get_ip(self, request) -> str:
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')


class DisputeDisbursementView(ConfirmDisbursementView):
    """Record that a member did not receive a disbursed loan.

    POST only, for the same reason as ConfirmDisbursementView.
    """

    def post(self, request):
        loan = self._get_loan_from_token(request)
        if isinstance(loan, Response):
            return loan

        reason = request.data.get('reason', '')

        with transaction.atomic():
            loan = (
                Loan.objects.select_for_update()
                .select_related(
                    'membership',
                    'membership__sacco',
                    'membership__user',
                )
                .get(id=loan.id)
            )
            if loan.disbursement_status == Loan.DisbursementStatus.DISPUTED:
                return Response(
                    {
                        'status': 'disputed',
                        'message': (
                            'Your dispute has been logged. '
                            'We will investigate.'
                        ),
                    },
                    status=status.HTTP_200_OK,
                )
            if loan.disbursement_status in [
                Loan.DisbursementStatus.MEMBER_CONFIRMED,
                Loan.DisbursementStatus.AUTO_CONFIRMED,
            ]:
                return Response(
                    {
                        'detail': (
                            'This disbursement has already been confirmed.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            loan.disbursement_status = Loan.DisbursementStatus.DISPUTED
            loan.member_disputed_at = timezone.now()
            loan.dispute_reason = reason
            loan.save(
                update_fields=[
                    'disbursement_status',
                    'member_disputed_at',
                    'dispute_reason',
                    'updated_at',
                ],
            )

            details = {
                'reason': reason,
                'disputed_at': loan.member_disputed_at.isoformat(),
            }
            DisbursementAuditLog.objects.create(
                loan=loan,
                event='MEMBER_DISPUTED',
                actor=None,
                actor_role='member',
                ip_address=self._get_ip(request),
                details=details,
            )
            DisbursementAuditLog.objects.create(
                loan=loan,
                event='ESCALATED_TO_SUPERADMIN',
                actor=None,
                actor_role='system',
                details={
                    'reason': 'member disputed receipt',
                    'member_reason': reason,
                },
            )

        title = 'Disbursement Dispute Raised'
        message = (
            f'Member {loan.membership.user.email} reported non-receipt '
            f'for loan {loan.id} at {loan.membership.sacco.name}.'
        )
        _notify_superadmins(title, message, related_loan_id=str(loan.id))
        _notify_sacco_admins(
            loan.membership.sacco,
            title,
            (
                f'Loan {loan.id} is under member dispute. Please preserve '
                f'all disbursement records for investigation.'
            ),
        )

        return Response(
            {
                'status': 'disputed',
                'message': (
                    'Your dispute has been logged. We will investigate.'
                ),
            },
            status=status.HTTP_200_OK,
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

        # Single source of truth: the same all-SACCO 0.5x-savings formula
        # the signals persist (services/engines/guarantor_logic.py).
        capacity = update_guarantee_capacity(guarantor_user)

        # ODPC: this response discloses another member's savings total and
        # guarantee capacity to the loan applicant.
        try:
            create_data_consent_log(
                user=guarantor_user,
                accessed_by=request.user,
                data_type='GUARANTOR_SAVINGS_DISCLOSURE',
                reason=(
                    'Guarantor savings total and guarantee capacity '
                    f'disclosed to the applicant for loan {loan.id}.'
                ),
                request=request,
            )
        except ConsentLogWriteError:
            pass

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
        """Find a possible guarantor user by phone or member number.

        Uses exact matching only for security. Partial matching was removed to
        prevent accidental selection of the wrong guarantor. Users must know
        the exact phone number or member number to search for a guarantor.
        """
        memberships = Membership.objects.select_related('user').filter(
            sacco=loan.membership.sacco,
            status=Membership.Status.APPROVED,
        )

        if phone:
            membership = memberships.filter(
                user__phone_number=phone,
            ).first()

            if membership is not None:
                return membership.user

        if member_number:
            membership = memberships.filter(
                member_number__iexact=member_number,
            ).first()

            if membership is not None:
                return membership.user

        return None


class GuarantorRequestView(APIView):
    """Request a member to guarantee a loan."""

    permission_classes = [IsAuthenticated]

    def post(self, request, loan_id):
        """Create a pending guarantor request for a loan."""
        loan = get_object_or_404(
            Loan.objects.select_related(
                'membership', 'membership__user', 'membership__sacco',
            ),
            id=loan_id,
            membership__user=request.user,
        )

        sacco_settings = getattr(loan.membership.sacco, 'settings', None)
        if (
            sacco_settings is not None
            and sacco_settings.guarantor_type_allowed
            == SaccoSettings.GuarantorTypeAllowed.EXTERNAL_ONLY
        ):
            return Response(
                {
                    'detail': (
                        'This SACCO only accepts external guarantors for '
                        'this loan.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
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

        with transaction.atomic():
            # Lock the guarantor's capacity row so this check reads a
            # state consistent with any concurrent approval for the same
            # guarantor. Same GuaranteeCapacity row, same lock, as the
            # approval path below.
            lock_guarantee_capacity(guarantor_user)
            capacity_data = calculate_guarantee_capacity(guarantor_user)
            if capacity_data['available_capacity'] < guarantee_amount:
                return Response(
                    {'detail': 'Guarantor has insufficient capacity.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if Guarantor.objects.filter(
                loan=loan,
                guarantor=guarantor_user,
            ).exists():
                return Response(
                    {'detail': 'Guarantor request already exists.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # The .exists() check above is a fast path; the unique_together
            # (loan, guarantor) on Guarantor is the real guard. A nested
            # savepoint lets a concurrent duplicate surface as a clean 400
            # instead of a 500, without breaking the outer transaction.
            try:
                with transaction.atomic():
                    guarantor = Guarantor.objects.create(
                        loan=loan,
                        guarantor=guarantor_user,
                        guarantee_amount=guarantee_amount,
                        status=Guarantor.Status.PENDING,
                    )
            except IntegrityError:
                return Response(
                    {'detail': 'Guarantor request already exists.'},
                    status=status.HTTP_400_BAD_REQUEST,
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
    """Guarantor approval or decline of a guarantee request.

    Capacity is enforced inline in the APPROVE branch under a row lock on
    GuaranteeCapacity (see below); there is no separate permission class
    for it. A permission check would run before that transaction, on a
    stale read, and would also wrongly gate DECLINE.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, loan_id, guarantor_id):
        """
        Record guarantor approval or decline.

        APPROVE: Validate capacity, update status, then re-check the
        shared guarantor-readiness gate (check_loan_guarantors_complete
        - both count AND coverage). If it passes, move the loan to
        PENDING_APPROVAL; otherwise leave it GUARANTORS_PENDING.

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
                # Lock this guarantor's capacity row, then read it under
                # the lock. A concurrent approval for another loan blocks
                # here until we commit update_guarantee_capacity() below,
                # then reads an available_capacity that already counts
                # this guarantee - so the two cannot jointly over-commit
                # the guarantor.
                capacity = lock_guarantee_capacity(request.user)

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

                # Re-check the SAME gate the final approval step uses -
                # count AND coverage - so the loan only advances when it
                # is genuinely review-ready, never merely "enough heads".
                is_ready, _reason = check_loan_guarantors_complete(loan)
                if (
                    is_ready
                    and loan.status != Loan.Status.PENDING_APPROVAL
                ):
                    loan.status = Loan.Status.PENDING_APPROVAL
                    loan.save(update_fields=['status', 'updated_at'])

                    # Notify SACCO admin that loan is ready for review.
                    self._notify_sacco_admin_review_ready(loan)

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

    def _notify_sacco_admin_review_ready(self, loan):
        """Notify each SACCO admin that the loan is ready for review."""
        from saccomanagement.models import Role

        admin_roles = Role.objects.filter(
            name=Role.SACCO_ADMIN,
            sacco=loan.membership.sacco,
            is_active=True,
        ).select_related('user')

        applicant_name = (
            f'{loan.membership.user.first_name} '
            f'{loan.membership.user.last_name}'
        )
        for role in admin_roles:
            create_notification(
                user=role.user,
                title='Loan Ready for Review',
                message=(
                    f'Loan of KES {loan.amount:.2f} from {applicant_name} '
                    f'has full guarantor cover and is ready for approval '
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


class LiquidityStatusView(APIView):
    """Return the current liquidity risk snapshot for a SACCO admin."""

    permission_classes = [IsSaccoAdmin]

    def get(self, request):
        sacco = self._get_sacco(request)
        if sacco is None:
            return Response(
                {
                    'success': False,
                    'message': 'SACCO context is required.',
                    'error_code': 'SACCO_CONTEXT_REQUIRED',
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        risk = check_liquidity_risk(sacco)
        alerts = LiquidityAlert.objects.filter(sacco=sacco).order_by(
            '-created_at',
        )[:5]

        return Response(
            {
                'success': True,
                'data': {
                    'sacco_id': str(sacco.id),
                    'sacco_name': sacco.name,
                    'current': self._serialize_risk(risk),
                    'recent_alerts': [
                        self._serialize_alert(alert)
                        for alert in alerts
                    ],
                },
            }
        )

    def _get_sacco(self, request):
        current_sacco = getattr(request, 'current_sacco', None)
        if current_sacco is not None:
            return current_sacco

        role = request.user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
            is_active=True,
        ).select_related('sacco').first()

        if role:
            return role.sacco

        return None

    def _serialize_risk(self, risk):
        return {
            'available_reserves': self._decimal_to_string(
                risk['available_reserves'],
            ),
            'pending_disbursements': self._decimal_to_string(
                risk['pending_disbursements'],
            ),
            'utilisation_pct': self._decimal_to_string(
                risk['utilisation_pct'],
            ),
            'at_risk': risk['at_risk'],
        }

    def _serialize_alert(self, alert):
        return {
            'id': str(alert.id),
            'available_reserves': self._decimal_to_string(
                alert.available_reserves,
            ),
            'pending_disbursements': self._decimal_to_string(
                alert.pending_disbursements,
            ),
            'utilisation_pct': self._decimal_to_string(
                alert.utilisation_pct,
            ),
            'resolved': alert.resolved,
            'resolved_at': (
                alert.resolved_at.isoformat()
                if alert.resolved_at else None
            ),
            'created_at': alert.created_at.isoformat(),
        }

    def _decimal_to_string(self, value):
        return str(value.quantize(Decimal('0.01')))


class NPLDashboardView(APIView):
    """Return unresolved NPL warning counts and portfolio ratio."""

    permission_classes = [IsSaccoAdmin]

    def get(self, request):
        sacco = self._get_sacco(request)
        if sacco is None:
            return Response(
                {
                    'success': False,
                    'message': 'SACCO context is required.',
                    'error_code': 'SACCO_CONTEXT_REQUIRED',
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        active_loans = Loan.objects.filter(
            membership__sacco=sacco,
            status=Loan.Status.ACTIVE,
        )
        npl_loans = active_loans.filter(
            npl_flags__resolved=False,
        ).distinct()
        total_outstanding = self._sum_outstanding(active_loans)
        npl_outstanding = self._sum_outstanding(npl_loans)
        counts = self._get_unresolved_counts(sacco)

        return Response(
            {
                'success': True,
                'data': {
                    'sacco_id': str(sacco.id),
                    'sacco_name': sacco.name,
                    'unresolved_counts': counts,
                    'npl_outstanding_balance': self._decimal_to_string(
                        npl_outstanding,
                    ),
                    'active_outstanding_balance': self._decimal_to_string(
                        total_outstanding,
                    ),
                    'npl_ratio': self._decimal_to_string(
                        self._calculate_ratio(
                            npl_outstanding,
                            total_outstanding,
                        ),
                        places='0.0001',
                    ),
                },
            }
        )

    def _get_sacco(self, request):
        current_sacco = getattr(request, 'current_sacco', None)
        if current_sacco is not None:
            return current_sacco

        role = request.user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
            is_active=True,
        ).select_related('sacco').first()

        if role:
            return role.sacco

        return None

    def _get_unresolved_counts(self, sacco):
        grouped_counts = NPLFlag.objects.filter(
            loan__membership__sacco=sacco,
            resolved=False,
        ).values('threshold_days').annotate(
            count=Count('id'),
        )
        counts = {'30': 0, '60': 0, '90': 0}

        for row in grouped_counts:
            counts[str(row['threshold_days'])] = row['count']

        return counts

    def _sum_outstanding(self, queryset):
        return (
            queryset.aggregate(total=Sum('outstanding_balance'))['total']
            or Decimal('0.00')
        )

    def _calculate_ratio(self, npl_outstanding, total_outstanding):
        if total_outstanding == Decimal('0.00'):
            return Decimal('0.0000')

        return npl_outstanding / total_outstanding

    def _decimal_to_string(self, value, places='0.01'):
        return str(value.quantize(Decimal(places)))


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
        if loan.status not in [
            Loan.Status.APPROVED,
            Loan.Status.ACTIVE,
            Loan.Status.DISBURSEMENT_PENDING,
        ]:
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
            is_active=True,
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

        # ODPC: a CRB check discloses the member's credit standing to the
        # SACCO admin. Logged once here (covers both a fresh check and a
        # cached hit below); a log failure must not block the check.
        try:
            create_data_consent_log(
                user=loan.membership.user,
                accessed_by=request.user,
                data_type='CRB_CHECK',
                reason=f'CRB credit check performed for loan {loan.id}.',
                request=request,
            )
        except ConsentLogWriteError:
            pass

        # Check for existing recent CRB check (within 30 days)
        force_refresh = (
            request.query_params.get('force_refresh', 'false').lower()
            == 'true'
        )
        
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




class DividendDeclarationListCreateView(SaccoScopedMixin, ListCreateAPIView):
    """List and create dividend declarations for a SACCO."""

    serializer_class = DividendDeclarationSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    pagination_class = None
    # Strict for the POST (create); GET list still accepts the fallback.
    require_sacco_header = True

    def get(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().post(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response({'success': True, 'data': serializer.data})

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['sacco'] = self.get_sacco_context()
        return context

    def get_queryset(self):
        return self.apply_sacco_scope(
            DividendDeclaration.objects.select_related(
                'sacco',
                'savings_type',
                'approved_by',
            )
        )


class DividendDeclarationDetailView(
    SaccoScopedMixin,
    RetrieveUpdateDestroyAPIView,
):
    """Retrieve, update, or delete a dividend declaration."""

    # Strict for PUT/PATCH/DELETE; GET retrieve still accepts the fallback.
    require_sacco_header = True
    serializer_class = DividendDeclarationSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    lookup_url_kwarg = 'uuid'

    def get(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().put(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().patch(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().delete(request, *args, **kwargs)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['sacco'] = self.get_sacco_context()
        return context

    def get_queryset(self):
        return self.apply_sacco_scope(
            DividendDeclaration.objects.select_related(
                'sacco',
                'savings_type',
                'approved_by',
            )
        )

    def get_object(self):
        if 'pk' in self.kwargs and 'uuid' not in self.kwargs:
            self.kwargs['uuid'] = self.kwargs['pk']
        return super().get_object()

    def perform_update(self, serializer):
        declaration = self.get_object()

        if declaration.status != DividendDeclaration.Status.DRAFT:
            from rest_framework import serializers

            raise serializers.ValidationError(
                'Can only edit declarations in DRAFT status.'
            )

        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != DividendDeclaration.Status.DRAFT:
            from rest_framework import serializers

            raise serializers.ValidationError(
                'Can only delete declarations in DRAFT status.'
            )

        instance.delete()


class DividendCalculateView(SaccoScopedMixin, APIView):
    """Queue the per-member dividend calculation for a declaration.

    The expensive O(members x months) loop runs in
    ``services.tasks.calculate_dividends_for_declaration_task``; the view
    only does the cheap status guard (kept a synchronous 400/409) and
    moves the declaration to ``CALCULATING`` before enqueuing.
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    require_sacco_header = True

    def post(self, request, uuid=None, pk=None):
        response = self._set_sacco_context()
        if response:
            return response

        from .tasks import calculate_dividends_for_declaration_task

        with transaction.atomic():
            declaration = get_object_or_404(
                self.apply_sacco_scope(
                    DividendDeclaration.objects.select_for_update().filter(
                        id=uuid or pk,
                    )
                )
            )

            # Fast, synchronous validation - a cheap status check that
            # must stay a 4xx, never an async FAILED run.
            if declaration.status in (
                DividendDeclaration.Status.APPROVED,
                DividendDeclaration.Status.DISBURSING,
                DividendDeclaration.Status.DISBURSED,
            ):
                return Response(
                    {
                        'detail': (
                            'Cannot recalculate dividends for a declaration '
                            f'in {declaration.status} status.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if declaration.status == DividendDeclaration.Status.CALCULATING:
                return Response(
                    {'detail': 'A dividend calculation is already running.'},
                    status=status.HTTP_409_CONFLICT,
                )

            declaration.status = DividendDeclaration.Status.CALCULATING
            declaration.save(update_fields=['status'])

        declaration_id = str(declaration.id)
        actor_id = str(request.user.id)
        transaction.on_commit(
            lambda: calculate_dividends_for_declaration_task.delay(
                declaration_id, actor_id,
            )
        )

        return Response(
            {
                'id': declaration_id,
                'status': declaration.status,
                'detail': 'Dividend calculation has been queued.',
            },
            status=status.HTTP_202_ACCEPTED,
        )


def _dividend_dual_control_enforced(sacco):
    """Whether this SACCO requires separate admins across dividend steps.

    Per-SACCO via ``SaccoSettings.enforce_dividend_dual_control``;
    defaults to enforced when the SACCO has no settings row yet.
    """
    sacco_settings = getattr(sacco, 'settings', None)
    if sacco_settings is None:
        return True
    return sacco_settings.enforce_dividend_dual_control


class DividendApproveView(SaccoScopedMixin, APIView):
    """Approve a calculated dividend declaration.

    Segregation of duties: the approver must not be the admin who created
    the declaration (unless the SACCO has disabled
    ``enforce_dividend_dual_control``).
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    require_sacco_header = True

    def post(self, request, uuid=None, pk=None):
        response = self._set_sacco_context()
        if response:
            return response

        with transaction.atomic():
            declaration = get_object_or_404(
                self.apply_sacco_scope(
                    DividendDeclaration.objects.select_for_update().filter(
                        id=uuid or pk,
                    )
                )
            )

            if declaration.status != DividendDeclaration.Status.CALCULATED:
                return Response(
                    {
                        'detail': (
                            'Can only approve declarations in CALCULATED '
                            'status.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Four-eyes: a declaration cannot be approved by whoever
            # created it. Compared on the authenticated user id, so
            # swapping X-Sacco-ID does not route around it (a mismatched
            # tenant just 404s the declaration above).
            if (
                declaration.created_by_id is not None
                and declaration.created_by_id == request.user.id
                and _dividend_dual_control_enforced(declaration.sacco)
            ):
                return Response(
                    {
                        'detail': (
                            'A dividend declaration must be approved by a '
                            'different admin than the one who created it.'
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            declaration.status = DividendDeclaration.Status.APPROVED
            declaration.approved_by = request.user
            declaration.save(update_fields=['status', 'approved_by'])

        log_audit(
            request.user,
            'DIVIDEND_APPROVED',
            'DividendDeclaration',
            declaration.id,
            old_values={'status': DividendDeclaration.Status.CALCULATED},
            new_values={
                'status': declaration.status,
                'sacco_id': str(declaration.sacco_id),
            },
            request=request,
        )

        return Response(
            {
                'id': str(declaration.id),
                'status': declaration.status,
                'approved_by': request.user.email,
            }
        )


class DividendDisburseView(SaccoScopedMixin, APIView):
    """Queue disbursement of an approved dividend declaration.

    The batched, all-or-nothing ledger run lives in
    ``services.tasks.disburse_dividends_for_declaration_task``; the view
    only does the synchronous APPROVED-status guard and moves the
    declaration to ``DISBURSING`` before enqueuing.

    Segregation of duties: the disburser must not be the admin who
    approved the declaration (unless the SACCO has disabled
    ``enforce_dividend_dual_control``).
    """

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    require_sacco_header = True

    def post(self, request, uuid=None, pk=None):
        response = self._set_sacco_context()
        if response:
            return response

        from .tasks import disburse_dividends_for_declaration_task

        with transaction.atomic():
            declaration = get_object_or_404(
                self.apply_sacco_scope(
                    DividendDeclaration.objects.select_for_update().filter(
                        id=uuid or pk,
                    )
                )
            )

            if declaration.status == DividendDeclaration.Status.DISBURSING:
                return Response(
                    {'detail': 'A disbursement is already running.'},
                    status=status.HTTP_409_CONFLICT,
                )
            if declaration.status != DividendDeclaration.Status.APPROVED:
                return Response(
                    {
                        'detail': (
                            'Can only disburse declarations in APPROVED '
                            'status.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Four-eyes: a declaration cannot be disbursed by whoever
            # approved it.
            if (
                declaration.approved_by_id is not None
                and declaration.approved_by_id == request.user.id
                and _dividend_dual_control_enforced(declaration.sacco)
            ):
                return Response(
                    {
                        'detail': (
                            'A dividend declaration must be disbursed by a '
                            'different admin than the one who approved it.'
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            declaration.status = DividendDeclaration.Status.DISBURSING
            declaration.save(update_fields=['status'])

        declaration_id = str(declaration.id)
        actor_id = str(request.user.id)
        transaction.on_commit(
            lambda: disburse_dividends_for_declaration_task.delay(
                declaration_id, actor_id,
            )
        )

        return Response(
            {
                'id': declaration_id,
                'status': declaration.status,
                'detail': 'Dividend disbursement has been queued.',
            },
            status=status.HTTP_202_ACCEPTED,
        )


class DividendPayoutListView(SaccoScopedMixin, ListAPIView):
    """List dividend payouts for a SACCO, filterable by declaration."""

    serializer_class = DividendPayoutSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    pagination_class = None

    def get(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response({'success': True, 'data': serializer.data})

    def get_queryset(self):
        queryset = self.get_sacco_queryset(
            DividendPayout.objects.select_related(
                'declaration',
                'membership__user',
                'saving',
            ),
            sacco_field='declaration__sacco',
        )

        declaration_id = self.request.query_params.get('declaration')
        if declaration_id:
            queryset = queryset.filter(declaration_id=declaration_id)

        return queryset.order_by('-created_at')


class LoanDisbursementAuditView(APIView):
    """Return full DisbursementAuditLog for a loan."""

    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]

    def get(self, request, loan_id):
        loan = get_object_or_404(Loan.objects.select_related('membership__sacco'), id=loan_id)
        self._check_access(request, loan)

        audit_logs = DisbursementAuditLog.objects.filter(loan=loan).order_by('created_at')
        serializer = DisbursementAuditSerializer({
            'loan_id': loan.id,
            'current_status': loan.disbursement_status,
            'mpesa_conversation_id': loan.mpesa_conversation_id or '',
            'mpesa_transaction_id': loan.mpesa_transaction_id or '',
            'audit_log': audit_logs,
        })
        return Response(serializer.data)

    def _check_access(self, request, loan):
        if request.user.is_staff or request.user.roles.filter(
            name=Role.SUPER_ADMIN, is_active=True,
        ).exists():
            return
        admin_sacco_ids = request.user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
            is_active=True,
        ).values_list('sacco_id', flat=True)
        if loan.membership.sacco_id not in admin_sacco_ids:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You do not have access to this loan.')
