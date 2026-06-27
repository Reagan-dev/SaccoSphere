from rest_framework import serializers

from .membership_doc_validators import validate_membership_document
from .models import MembershipDocument, SaccoApplication


class MembershipDocumentUploadSerializer(serializers.ModelSerializer):
    application_id = serializers.UUIDField(write_only=True)
    document_type = serializers.ChoiceField(
        choices=MembershipDocument.DocumentType.choices,
    )
    file = serializers.FileField(
        validators=[validate_membership_document],
    )
    notes = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    class Meta:
        model = MembershipDocument
        fields = (
            'application_id',
            'document_type',
            'file',
            'notes',
        )

    def validate(self, attrs):
        request = self.context['request']
        application = self._get_owned_application(
            attrs['application_id'],
            request.user,
        )

        allowed_statuses = (
            SaccoApplication.Status.DRAFT,
            SaccoApplication.Status.SUBMITTED,
        )
        if application.status not in allowed_statuses:
            raise serializers.ValidationError(
                {
                    'application_id': (
                        'Documents can only be uploaded for draft or '
                        'submitted applications.'
                    ),
                }
            )

        attrs['application'] = application
        return attrs

    def create(self, validated_data):
        validated_data.pop('application_id')
        return MembershipDocument.objects.create(**validated_data)

    def _get_owned_application(self, application_id, user):
        try:
            return SaccoApplication.objects.get(
                id=application_id,
                user=user,
            )
        except SaccoApplication.DoesNotExist as exc:
            raise serializers.ValidationError(
                {'application_id': 'Application not found for this user.'}
            ) from exc


class MembershipDocumentDetailSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = MembershipDocument
        fields = (
            'id',
            'application',
            'document_type',
            'file',
            'file_url',
            'file_name',
            'file_size_bytes',
            'notes',
            'is_verified',
            'uploaded_at',
        )
        read_only_fields = fields

    def get_file_url(self, obj):
        if not obj.file:
            return None

        request = self.context.get('request')
        if request is None:
            return obj.file.url

        return request.build_absolute_uri(obj.file.url)
