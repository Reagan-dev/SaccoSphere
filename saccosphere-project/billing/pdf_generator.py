"""PDF rendering for SaccoSphere monthly invoices."""

import os
from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone


class InvoicePDFGenerator:
    """Generates PDF invoice using WeasyPrint."""

    def generate(self, invoice) -> bytes:
        """Return rendered PDF bytes for an invoice."""
        from weasyprint import HTML

        line_items = invoice.invoicelineitem_set.all().order_by('created_at')
        by_type = {}

        for item in line_items:
            if item.transaction_type not in by_type:
                by_type[item.transaction_type] = {
                    'count': 0,
                    'total_fee': Decimal('0'),
                    'total_gross_amount': Decimal('0'),
                }

            by_type[item.transaction_type]['count'] += 1
            by_type[item.transaction_type]['total_fee'] += item.platform_fee
            by_type[item.transaction_type][
                'total_gross_amount'
            ] += item.gross_amount

        context = {
            'invoice': invoice,
            'sacco': invoice.sacco,
            'line_items': line_items,
            'by_type': by_type,
            'payment_account_name': settings.BILLING_ACCOUNT_NAME,
            'payment_paybill': settings.BILLING_PAYBILL,
            'payment_account_no': settings.BILLING_ACCOUNT_NUMBER,
            'due_date': invoice.due_date,
            'generated_at': timezone.now(),
        }
        html_string = render_to_string(
            'billing/invoice_template.html',
            context,
        )
        return HTML(string=html_string).write_pdf()

    def save(self, invoice, pdf_bytes: bytes) -> str:
        """Save PDF to disk and return the absolute path."""
        path = os.path.join(
            settings.MEDIA_ROOT,
            'invoices',
            str(invoice.sacco.id),
            f'{invoice.invoice_number}.pdf',
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, 'wb') as pdf_file:
            pdf_file.write(pdf_bytes)

        invoice.pdf_path = path
        invoice.save(update_fields=['pdf_path'])
        return path
