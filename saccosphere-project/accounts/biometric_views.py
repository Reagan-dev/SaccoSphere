from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserDevice
from .serializers import DeviceListSerializer, DeviceRegistrationSerializer


class DeviceRegistrationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        device, created = UserDevice.objects.get_or_create(
            user=request.user,
            device_id=data['device_id'],
            defaults={
                'device_name': data.get('device_name'),
                'platform': data['platform'],
                'push_token': data.get('push_token'),
                'biometric_enabled': data['biometric_enabled'],
            },
        )

        if not created:
            device.device_name = data.get('device_name')
            device.platform = data['platform']
            device.push_token = data.get('push_token')
            device.biometric_enabled = data['biometric_enabled']
            device.save(
                update_fields=[
                    'device_name',
                    'platform',
                    'push_token',
                    'biometric_enabled',
                    'last_seen',
                ],
            )

        return Response(
            {
                'device_registered': True,
                'message': (
                    'Biometric login is now enabled for this device.'
                ),
            },
            status=status.HTTP_200_OK,
        )


class DeviceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        devices = request.user.devices.order_by('-last_seen')
        serializer = DeviceListSerializer(devices, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class RevokeDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, device_id):
        try:
            device = UserDevice.objects.get(
                user=request.user,
                device_id=device_id,
            )
        except UserDevice.DoesNotExist:
            return Response(
                {'detail': 'Device not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        device.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
