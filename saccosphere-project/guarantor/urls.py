from django.urls import path

from .external_views import ExternalGuarantorRespondView


app_name = 'guarantor'

urlpatterns = [
    path(
        'external/respond/<str:response_token>/',
        ExternalGuarantorRespondView.as_view(),
        name='external-guarantor-respond',
    ),
]
