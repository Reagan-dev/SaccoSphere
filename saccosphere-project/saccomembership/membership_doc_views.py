from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import CreateAPIView, DestroyAPIView, ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from saccomanagement.models import Role

from .membership_doc_serializers import (
    MembershipDocumentDetailSerializer,
    MembershipDocumentUploadSerializer,
)
from .models import MembershipDocument, SaccoApplication


class MembershipDocumentUploadView(CreateAPIView):
    serializer_class = MembershipDocumentUploadSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer(self, *args, **kwargs):
        data = kwargs.get('data')
        if data is not None:
            copied_data = data.copy()
            copied_data['application_id'] = self.kwargs['application_id']
            kwargs['data'] = copied_data
        return super().get_serializer(*args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data['file']
        document = serializer.save(
            file_name=upload.name[:100],
            file_size_bytes=upload.size,
        )
        response_serializer = MembershipDocumentDetailSerializer(
            document,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


class MembershipDocumentListView(ListAPIView):
    serializer_class = MembershipDocumentDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        application = self._get_authorized_application()
        return MembershipDocument.objects.filter(
            application=application,
        ).select_related('application', 'application__sacco')

    def _get_authorized_application(self):
        application = get_object_or_404(
            SaccoApplication.objects.select_related('user', 'sacco'),
            id=self.kwargs['application_id'],
        )

        if application.user == self.request.user:
            return application

        if self._is_sacco_admin(application):
            return application

        raise PermissionDenied(
            'You do not have permission to view these documents.'
        )

    def _is_sacco_admin(self, application):
        return Role.objects.filter(
            user=self.request.user,
            sacco=application.sacco,
            name=Role.SACCO_ADMIN,
        ).exists()


class MembershipDocumentCollectionView(
    MembershipDocumentUploadView,
    MembershipDocumentListView,
):
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return MembershipDocumentUploadSerializer
        return MembershipDocumentDetailSerializer

    def get(self, request, *args, **kwargs):
        return MembershipDocumentListView.get(
            self,
            request,
            *args,
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        return MembershipDocumentUploadView.post(
            self,
            request,
            *args,
            **kwargs,
        )


class MembershipDocumentDeleteView(DestroyAPIView):
    serializer_class = MembershipDocumentDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'
    lookup_url_kwarg = 'id'

    def get_queryset(self):
        return MembershipDocument.objects.select_related(
            'application',
            'application__user',
        ).filter(
            application_id=self.kwargs['application_id'],
            application__user=self.request.user,
        )

    def destroy(self, request, *args, **kwargs):
        document = self.get_object()
        if document.application.status != SaccoApplication.Status.DRAFT:
            raise ValidationError(
                {
                    'detail': (
                        'Documents can only be deleted while the '
                        'application is in draft status.'
                    ),
                }
            )

        self.perform_destroy(document)
        return Response(status=status.HTTP_204_NO_CONTENT)
