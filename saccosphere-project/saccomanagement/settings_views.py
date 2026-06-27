"""SACCO admin settings endpoints."""

from decimal import Decimal

from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import SaccoSettings
from accounts.permissions import IsSaccoAdmin

from .mixins import SaccoScopedMixin
from .serializers import SaccoSettingsSerializer


class SaccoSettingsView(SaccoScopedMixin, RetrieveUpdateAPIView):
    """
    Retrieve or update SACCO-specific configuration.

    GET/PATCH /api/v1/management/settings/
    """

    serializer_class = SaccoSettingsSerializer
    permission_classes = [IsAuthenticated, IsSaccoAdmin]
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
            from rest_framework.exceptions import ValidationError

            raise ValidationError({'detail': 'SACCO context is required.'})

        settings, _ = SaccoSettings.objects.get_or_create(
            sacco=sacco,
            defaults={
                'registration_fee': sacco.registration_fee,
                'loan_multiplier': int(sacco.loan_multiplier),
            },
        )
        return settings

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
        serializer.save()

        sacco = instance.sacco
        sync_fields = {}
        if 'registration_fee' in serializer.validated_data:
            sync_fields['registration_fee'] = instance.registration_fee
        if 'loan_multiplier' in serializer.validated_data:
            sync_fields['loan_multiplier'] = Decimal(instance.loan_multiplier)
        if sync_fields:
            for field, value in sync_fields.items():
                setattr(sacco, field, value)
            sacco.save(update_fields=list(sync_fields.keys()) + ['updated_at'])

        return Response(
            {
                'success': True,
                'message': 'SACCO settings updated.',
                'data': serializer.data,
            },
        )
