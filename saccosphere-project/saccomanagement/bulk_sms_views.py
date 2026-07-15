"""SACCO admin bulk SMS campaign endpoints."""

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin
from saccomembership.models import Membership
from services.models import SavingsType

from .models import Role, SMSCampaign, SMSCampaignRecipient


class BulkSMSBaseView(APIView):
    """Shared SACCO scoping helpers for bulk SMS endpoints."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]

    def get_sacco(self, request):
        current_sacco = getattr(request, 'current_sacco', None)
        if current_sacco is not None:
            return current_sacco

        role = request.user.roles.filter(
            name=Role.SACCO_ADMIN,
            sacco__isnull=False,
        ).select_related('sacco').first()

        if role:
            return role.sacco

        return None

    def get_sacco_or_error(self, request):
        sacco = self.get_sacco(request)
        if sacco is not None:
            return sacco, None

        return None, Response(
            {
                'success': False,
                'message': 'SACCO context is required.',
                'error_code': 'SACCO_CONTEXT_REQUIRED',
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    def get_campaign_queryset(self, sacco):
        return SMSCampaign.objects.filter(sacco=sacco).select_related(
            'sacco',
            'created_by',
        )

    def serialize_campaign(self, campaign, include_recipients=False):
        data = {
            'id': str(campaign.id),
            'sacco_id': str(campaign.sacco_id),
            'message': campaign.message,
            'audience_filter': campaign.audience_filter,
            'status': campaign.status,
            'total_recipients': campaign.total_recipients,
            'sent_count': campaign.sent_count,
            'failed_count': campaign.failed_count,
            'created_by': (
                str(campaign.created_by_id)
                if campaign.created_by_id else None
            ),
            'created_at': campaign.created_at.isoformat(),
        }

        if include_recipients:
            data['recipients'] = [
                self.serialize_recipient(recipient)
                for recipient in campaign.recipients.select_related(
                    'membership__user',
                )
            ]

        return data

    def serialize_recipient(self, recipient):
        member = recipient.membership.user
        return {
            'id': str(recipient.id),
            'membership_id': str(recipient.membership_id),
            'member_name': member.get_full_name() or member.email,
            'phone_number': recipient.phone_number,
            'status': recipient.status,
            'sent_at': (
                recipient.sent_at.isoformat()
                if recipient.sent_at else None
            ),
            'error_message': recipient.error_message,
        }


class BulkSMSCreateView(BulkSMSBaseView):
    """Create a draft SMS campaign and recipient preview."""

    ALLOWED_FILTERS = {
        'status',
        'member_number',
        'savings_type',
    }

    def post(self, request):
        sacco, error_response = self.get_sacco_or_error(request)
        if error_response is not None:
            return error_response

        message = request.data.get('message', '')
        audience_filter = request.data.get('audience_filter') or {
            'status': Membership.Status.APPROVED,
        }
        validation_error = self.validate_payload(message, audience_filter)
        if validation_error:
            return validation_error

        try:
            recipients = list(
                self.resolve_audience(sacco, audience_filter),
            )
        except ValueError as exc:
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            campaign = SMSCampaign.objects.create(
                sacco=sacco,
                created_by=request.user,
                message=message,
                audience_filter=audience_filter,
                status=SMSCampaign.Status.DRAFT,
                total_recipients=len(recipients),
            )
            SMSCampaignRecipient.objects.bulk_create([
                SMSCampaignRecipient(
                    campaign=campaign,
                    membership=membership,
                    phone_number=membership.user.phone_number,
                )
                for membership in recipients
            ])

        return Response(
            {
                'success': True,
                'data': {
                    'id': str(campaign.id),
                    'status': campaign.status,
                    'total_recipients': campaign.total_recipients,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    def validate_payload(self, message, audience_filter):
        if not isinstance(message, str) or not message.strip():
            return Response(
                {'detail': 'message is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(message) > SMSCampaign.MAX_MESSAGE_LENGTH:
            return Response(
                {
                    'detail': (
                        'message cannot exceed '
                        f'{SMSCampaign.MAX_MESSAGE_LENGTH} characters.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(audience_filter, dict):
            return Response(
                {'detail': 'audience_filter must be an object.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        unsupported_keys = set(audience_filter) - self.ALLOWED_FILTERS
        if unsupported_keys:
            keys = ', '.join(sorted(unsupported_keys))
            return Response(
                {'detail': f'Unsupported audience filter: {keys}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return None

    def resolve_audience(self, sacco, audience_filter):
        queryset = Membership.objects.filter(
            sacco=sacco,
            user__phone_number__isnull=False,
        ).exclude(
            user__phone_number='',
        ).select_related(
            'user',
            'sacco',
        )

        status_filter = audience_filter.get('status')
        if status_filter is not None:
            if status_filter not in Membership.Status.values:
                raise ValueError('Invalid membership status filter.')
            queryset = queryset.filter(status=status_filter)

        member_number = audience_filter.get('member_number')
        if member_number is not None:
            queryset = queryset.filter(member_number=member_number)

        savings_type = audience_filter.get('savings_type')
        if savings_type is not None:
            if savings_type not in SavingsType.Name.values:
                raise ValueError('Invalid savings_type filter.')
            queryset = queryset.filter(
                saving__savings_type__name=savings_type,
            ).distinct()

        return queryset.order_by('created_at')


class BulkSMSSendView(BulkSMSBaseView):
    """Queue a draft campaign for background sending."""

    def post(self, request, id):
        from notifications.tasks import send_bulk_sms_campaign_task

        sacco, error_response = self.get_sacco_or_error(request)
        if error_response is not None:
            return error_response

        campaign = get_object_or_404(
            self.get_campaign_queryset(sacco),
            id=id,
        )
        if campaign.status != SMSCampaign.Status.DRAFT:
            return Response(
                {'detail': 'Only draft campaigns can be sent.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        campaign.status = SMSCampaign.Status.SENDING
        campaign.save(update_fields=['status'])
        send_bulk_sms_campaign_task.delay(str(campaign.id))

        return Response(
            {
                'success': True,
                'data': self.serialize_campaign(campaign),
            },
        )


class BulkSMSCampaignListView(BulkSMSBaseView):
    """List SMS campaigns for the current SACCO."""

    def get(self, request):
        sacco, error_response = self.get_sacco_or_error(request)
        if error_response is not None:
            return error_response

        campaigns = self.get_campaign_queryset(sacco).order_by('-created_at')
        return Response(
            {
                'success': True,
                'data': [
                    self.serialize_campaign(campaign)
                    for campaign in campaigns
                ],
            },
        )


class BulkSMSCampaignDetailView(BulkSMSBaseView):
    """Return a single SMS campaign and recipient statuses."""

    def get(self, request, id):
        sacco, error_response = self.get_sacco_or_error(request)
        if error_response is not None:
            return error_response

        campaign = get_object_or_404(
            self.get_campaign_queryset(sacco),
            id=id,
        )
        return Response(
            {
                'success': True,
                'data': self.serialize_campaign(
                    campaign,
                    include_recipients=True,
                ),
            },
        )


class BulkSMSCampaignCollectionView(
    BulkSMSCampaignListView,
    BulkSMSCreateView,
):
    """Expose campaign list and create behavior on the collection URL."""

    pass
