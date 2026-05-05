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
