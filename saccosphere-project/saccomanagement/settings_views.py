"""SACCO admin settings endpoints."""

from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Sacco, SaccoSettings
from accounts.permissions import IsSaccoAdminOrSuperAdmin

from .audit_logger import log_audit
from .mixins import SaccoScopedMixin
from .models import Role
from .serializers import SaccoSettingsSerializer


class SaccoSettingsView(SaccoScopedMixin, RetrieveUpdateAPIView):
    """
    Retrieve or update SACCO-specific configuration.

    GET/PATCH /api/v1/management/settings/
    A platform admin (SUPER_ADMIN or staff) has no "current" SACCO, so they
    must target one explicitly: ?sacco_id=<uuid>, mirroring the convention
    already used for platform-admin access elsewhere (see
    superadmin_views.AllMembersListView).
    """

    serializer_class = SaccoSettingsSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdminOrSuperAdmin]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().patch(request, *args, **kwargs)

    def get_object(self):
        sacco = self.get_sacco_context()
        if sacco is None:
            sacco = self._resolve_platform_admin_sacco()

        settings, _ = SaccoSettings.objects.get_or_create(
            sacco=sacco,
            defaults={
                'registration_fee': sacco.registration_fee,
                'loan_multiplier': int(sacco.loan_multiplier),
            },
        )
        return settings

    def _resolve_platform_admin_sacco(self):
        """
        get_sacco_context() returns None both for a genuine platform admin
        (by SaccoScopedMixin design) and, in principle, for anyone else who
        reaches here without a resolvable SACCO - so re-check platform
        admin status explicitly rather than assuming None means one thing.
        """
        user = self.request.user
        is_platform_admin = user.is_staff or Role.objects.filter(
            user=user,
            name=Role.SUPER_ADMIN,
            is_active=True,
        ).exists()

        if not is_platform_admin:
            raise ValidationError({'detail': 'SACCO context is required.'})

        sacco_id = self.request.query_params.get('sacco_id')
        if not sacco_id:
            raise ValidationError(
                {
                    'sacco_id': (
                        'sacco_id query parameter is required for '
                        'platform admins.'
                    ),
                },
            )

        try:
            return Sacco.objects.get(id=sacco_id)
        except Sacco.DoesNotExist:
            raise ValidationError({'sacco_id': 'Sacco not found.'})

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {
                'success': True,
                'data': serializer.data,
            },
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        # Capture before/after only for the fields actually submitted -
        # "what changed" matters more than "settings were touched."
        changed_fields = list(serializer.validated_data.keys())
        old_values = {
            field: str(getattr(instance, field)) for field in changed_fields
        }

        serializer.save()

        new_values = {
            field: str(getattr(instance, field)) for field in changed_fields
        }
        log_audit(
            request.user,
            'SACCO_SETTINGS_UPDATED',
            'SaccoSettings',
            instance.id,
            old_values=old_values,
            new_values=new_values,
            request=request,
        )

        sacco = instance.sacco
        sync_fields = {}
        if 'registration_fee' in serializer.validated_data:
            sync_fields['registration_fee'] = instance.registration_fee
        if 'loan_multiplier' in serializer.validated_data:
            sync_fields['loan_multiplier'] = Decimal(instance.loan_multiplier)
        if sync_fields:
            with transaction.atomic():
                for field, value in sync_fields.items():
                    setattr(sacco, field, value)
                sacco.save(
                    update_fields=list(sync_fields.keys()) + ['updated_at'],
                )

        return Response(
            {
                'success': True,
                'message': 'SACCO settings updated.',
                'data': serializer.data,
            },
        )
