from django.urls import path

from .membership_doc_views import (
    MembershipDocumentCollectionView,
    MembershipDocumentDeleteView,
)
from .views import (
    MembershipDetailView,
    MembershipLeaveView,
    MembershipListView,
    SaccoFieldsView,
)


app_name = 'saccomembership'

urlpatterns = [
    path(
        'memberships/',
        MembershipListView.as_view(),
        name='membership-list',
    ),
    path(
        'memberships/<uuid:id>/',
        MembershipDetailView.as_view(),
        name='membership-detail',
    ),
    path(
        'memberships/<uuid:id>/leave/',
        MembershipLeaveView.as_view(),
        name='membership-leave',
    ),
    path(
        'saccos/<uuid:sacco_id>/fields/',
        SaccoFieldsView.as_view(),
        name='sacco-fields',
    ),
    path(
        'applications/<uuid:application_id>/documents/',
        MembershipDocumentCollectionView.as_view(),
        name='membership-document-collection',
    ),
    path(
        'applications/<uuid:application_id>/documents/<uuid:id>/',
        MembershipDocumentDeleteView.as_view(),
        name='membership-document-delete',
    ),
]
