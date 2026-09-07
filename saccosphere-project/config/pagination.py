from math import ceil

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class SaccoSpherePagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100
    page_size_query_param = 'page_size'

    def get_paginated_response(self, data):
        count = self.page.paginator.count
        total_pages = ceil(count / self.get_page_size(self.request))

        return Response(
            {
                'success': True,
                'message': 'Success',
                'data': {
                    'count': count,
                    'total_pages': total_pages,
                    'current_page': self.page.number,
                    'next': self.get_next_link(),
                    'previous': self.get_previous_link(),
                    'results': data,
                },
            }
        )


class FinancialPagination(SaccoSpherePagination):
    page_size = 50


class NotificationPagination(SaccoSpherePagination):
    page_size = 30


class ConsentExportConsentsPagination(SaccoSpherePagination):
    """Paginates the UserConsent section of the consent data-export endpoint.

    Uses its own page/page_size query params (rather than the default
    'page'/'page_size') so the consents and audit-log sections of a single
    export response can be paged independently.
    """

    page_size = 50
    page_query_param = 'consents_page'
    page_size_query_param = 'consents_page_size'


class ConsentExportAuditLogPagination(SaccoSpherePagination):
    """Paginates the DataConsentLog section of the consent data-export endpoint.

    See ConsentExportConsentsPagination - kept as a separate paginator with
    its own query params for the same reason.
    """

    page_size = 50
    page_query_param = 'logs_page'
    page_size_query_param = 'logs_page_size'
