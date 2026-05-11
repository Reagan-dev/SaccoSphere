from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    GuarantorRequestView,
    GuarantorSearchView,
    LoanApplyView,
    LoanCollectionView,
    LoanDetailView,
    LoanEligibilityView,
    LoanListView,
    LoanTypeListView,
    RepaymentScheduleView,
    SavingListView,
    SavingsBreakdownView,
    SavingsTypeViewSet,
)


router = DefaultRouter()
router.register(
    'savings-types',
    SavingsTypeViewSet,
    basename='savings-type',
)


app_name = 'services'

urlpatterns = [
    path('', include(router.urls)),
    path('savings/', SavingListView.as_view(), name='saving-list'),
    path('savings/breakdown/', SavingsBreakdownView.as_view(), name='savings-breakdown'),
    path('loan-types/', LoanTypeListView.as_view(), name='loan-type-list'),
    path('loans/', LoanCollectionView.as_view(), name='loan-collection'),
    path(
        'loans/eligibility/',
        LoanEligibilityView.as_view(),
        name='loan-eligibility',
    ),
    path('loans/apply/', LoanApplyView.as_view(), name='loan-apply'),
    path('loans/list/', LoanListView.as_view(), name='loan-list'),
    path('loans/<uuid:id>/', LoanDetailView.as_view(), name='loan-detail'),
    path(
        'loans/<uuid:id>/schedule/',
        RepaymentScheduleView.as_view(),
        name='repayment-schedule',
    ),
    path(
        'loans/<uuid:loan_id>/guarantors/search/',
        GuarantorSearchView.as_view(),
        name='guarantor-search',
    ),
    path(
        'loans/<uuid:loan_id>/guarantors/',
        GuarantorRequestView.as_view(),
        name='guarantor-request',
    ),
]
