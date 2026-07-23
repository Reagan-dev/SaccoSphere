"""API views for SACCO member CSV/Excel import jobs."""

from rest_framework import serializers, status
from rest_framework.generics import RetrieveAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSaccoAdmin

from .import_utils import (
    MAX_IMPORT_FILE_SIZE,
    parse_import_file,
    process_import_job,
)
from .mixins import SaccoScopedMixin
from .models import MemberImportJob


class MemberImportJobSerializer(serializers.ModelSerializer):
    sacco_id = serializers.UUIDField(source='sacco.id', read_only=True)
    status = serializers.SerializerMethodField()
    progress_pct = serializers.SerializerMethodField()
    errors_summary = serializers.SerializerMethodField()

    class Meta:
        model = MemberImportJob
        fields = (
            'id',
            'sacco_id',
            'file_name',
            'status',
            'total_rows',
            'processed_rows',
            'success_rows',
            'error_rows',
            'progress_pct',
            'errors',
            'errors_summary',
            'started_at',
            'completed_at',
            'created_at',
        )
        read_only_fields = fields

    def get_progress_pct(self, obj):
        return obj.progress_pct

    def get_status(self, obj):
        status_map = {
            MemberImportJob.Status.PENDING: 'queued',
            MemberImportJob.Status.PROCESSING: 'processing',
            MemberImportJob.Status.COMPLETED: 'completed',
            MemberImportJob.Status.FAILED: 'failed',
        }
        return status_map[obj.status]

    def get_errors_summary(self, obj):
        return {
            'count': len(obj.errors),
            'items': obj.errors[:20],
        }


class MemberImportCreateView(SaccoScopedMixin, APIView):
    """Upload a CSV/XLSX file and enqueue a member import job."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    parser_classes = [MultiPartParser]

    def post(self, request):
        response = self._set_sacco_context()
        if response:
            return response

        sacco = self.get_sacco_context()
        if sacco is None:
            return Response(
                {'detail': 'SACCO context is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        upload = request.FILES.get('file')
        if upload is None:
            return Response(
                {'detail': 'file is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if upload.size > MAX_IMPORT_FILE_SIZE:
            return Response(
                {'detail': 'Import file must be smaller than 5MB.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rows, parse_error = parse_import_file(upload)
        if parse_error:
            return Response(
                {'detail': parse_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job = MemberImportJob.objects.create(
            sacco=sacco,
            created_by=request.user,
            file_name=upload.name[:100],
            total_rows=len(rows),
            status=MemberImportJob.Status.PENDING,
        )
        task_result = process_import_job.delay(str(job.id), rows=rows)

        return Response(
            {
                'job_id': str(job.id),
                'task_id': task_result.id,
                'status': 'queued',
                'message': (
                    f'Import queued. {len(rows)} rows ready for processing.'
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class MemberImportStatusView(SaccoScopedMixin, RetrieveAPIView):
    """Return progress and errors for one member import job."""

    permission_classes = [IsAuthenticated, IsSaccoAdmin]
    serializer_class = MemberImportJobSerializer
    lookup_field = 'id'
    lookup_url_kwarg = 'job_id'

    def get(self, request, *args, **kwargs):
        response = self._set_sacco_context()
        if response:
            return response
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = MemberImportJob.objects.select_related(
            'sacco',
            'created_by',
        )
        return self.get_sacco_queryset(queryset, sacco_field='sacco')

    def retrieve(self, request, *args, **kwargs):
        job = self.get_object()
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)
