from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class DashboardOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({})


class SaccoDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({})


class MemberDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({})
