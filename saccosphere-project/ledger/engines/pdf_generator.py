from copy import deepcopy

from django.template.loader import render_to_string
from django.utils import timezone


def generate_statement_pdf(statement_data):
    """
    Generate a PDF from statement data dict.

    Returns bytes of the PDF file.
    """
    from weasyprint import HTML

    html_string = render_to_string(
        'ledger/statement.html',
        _format_statement_data(statement_data),
    )
    return HTML(string=html_string).write_pdf()


def _format_statement_data(statement_data):
    data = deepcopy(statement_data)

    for field in (
        'opening_balance',
        'closing_balance',
        'total_credits',
        'total_debits',
    ):
        data[f'{field}_display'] = _format_money(data[field])

    data['generated_at'] = timezone.localtime(
        data['generated_at'],
    ).strftime('%Y-%m-%d %H:%M:%S')
    data['from_date'] = data['from_date'].strftime('%Y-%m-%d')
    data['to_date'] = data['to_date'].strftime('%Y-%m-%d')

    for entry in data['entries']:
        entry['created_at'] = timezone.localtime(
            entry['created_at'],
        ).strftime('%Y-%m-%d')
        entry['balance_after_display'] = _format_money(
            entry['balance_after'],
        )
        entry['debit_display'] = ''
        entry['credit_display'] = ''
        if entry['entry_type'] == 'DEBIT':
            entry['debit_display'] = f"KES {_format_money(entry['amount'])}"
        if entry['entry_type'] == 'CREDIT':
            entry['credit_display'] = f"KES {_format_money(entry['amount'])}"

    return data


def _format_money(amount):
    return f'{amount:,.2f}'
