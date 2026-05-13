"""Parsing utilities for SACCO member bulk import files."""

import pandas as pd


class ImportParseError(Exception):
    """Raised when a member import file cannot be parsed."""


EXPECTED_COLUMNS = [
    'first_name',
    'last_name',
    'email',
    'phone',
    'id_number',
    'member_number',
    'savings_amount',
    'savings_type',
    'loan_amount',
    'loan_status',
]


def parse_member_import_file(file_obj):
    """
    Parse a CSV or Excel file of member records.

    Returns:
        list[dict]: Raw row dictionaries with normalized column names.
    """
    filename = (getattr(file_obj, 'name', '') or '').lower()

    try:
        if filename.endswith('.xlsx') or filename.endswith('.xls'):
            dataframe = pd.read_excel(file_obj, dtype=str)
        else:
            dataframe = pd.read_csv(file_obj, dtype=str)
    except Exception as exc:
        raise ImportParseError(
            'Unable to read import file. Ensure it is valid CSV or Excel.',
        ) from exc

    normalized_columns = {
        column: str(column).strip().lower()
        for column in dataframe.columns
    }
    dataframe = dataframe.rename(columns=normalized_columns)

    missing_columns = [
        column for column in EXPECTED_COLUMNS if column not in dataframe.columns
    ]
    if missing_columns:
        raise ImportParseError(
            f'Missing required columns: {", ".join(missing_columns)}.',
        )

    for column in dataframe.columns:
        if pd.api.types.is_object_dtype(dataframe[column]):
            dataframe[column] = dataframe[column].apply(
                lambda value: value.strip()
                if isinstance(value, str)
                else value,
            )

    dataframe = dataframe.where(pd.notna(dataframe), None)
    return list(dataframe.to_dict(orient='records'))
