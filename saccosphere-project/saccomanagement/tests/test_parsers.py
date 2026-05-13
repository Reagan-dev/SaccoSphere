"""Tests for member import parsing and validation helpers."""

from io import StringIO

from django.test import SimpleTestCase

from saccomanagement.data_imports.parsers import parse_member_import_file
from saccomanagement.data_imports.validators import validate_import_file


class NamedStringIO(StringIO):
    """StringIO with a name attribute to simulate uploaded files."""

    def __init__(self, value, name):
        super().__init__(value)
        self.name = name


class MemberImportParserTests(SimpleTestCase):
    """Validate CSV parser and import-row validation behavior."""

    def _csv_with_row(self, phone='0712345678', id_number='12345678',
                      savings_amount='1000'):
        """Build a minimal CSV payload for parsing and validation tests."""
        return (
            'first_name,last_name,email,phone,id_number,member_number,'
            'savings_amount,savings_type,loan_amount,loan_status\n'
            f'Jane,Doe,jane@example.com,{phone},{id_number},M001,'
            f'{savings_amount},BOSA,5000,ACTIVE\n'
        )

    def test_valid_csv_parsed(self):
        """Valid CSV should parse into a list containing one member dict."""
        file_obj = NamedStringIO(self._csv_with_row(), 'members.csv')

        rows = parse_member_import_file(file_obj)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['first_name'], 'Jane')
        self.assertEqual(rows[0]['email'], 'jane@example.com')
        self.assertEqual(rows[0]['savings_type'], 'BOSA')

    def test_invalid_phone_flagged(self):
        """Invalid phone values should be reported in error rows."""
        file_obj = NamedStringIO(self._csv_with_row(phone='12345'), 'members.csv')
        rows = parse_member_import_file(file_obj)

        valid_rows, error_rows, summary = validate_import_file(rows)

        self.assertEqual(len(valid_rows), 0)
        self.assertEqual(len(error_rows), 1)
        self.assertTrue(
            any(
                'phone format is invalid' in error
                for error in error_rows[0]['errors']
            ),
        )
        self.assertEqual(summary['error_rows'], 1)

    def test_invalid_id_flagged(self):
        """Non-numeric id_number values should be reported as invalid."""
        file_obj = NamedStringIO(
            self._csv_with_row(id_number='ABC1234'),
            'members.csv',
        )
        rows = parse_member_import_file(file_obj)

        valid_rows, error_rows, summary = validate_import_file(rows)

        self.assertEqual(len(valid_rows), 0)
        self.assertEqual(len(error_rows), 1)
        self.assertTrue(
            any(
                'id_number format is invalid' in error
                for error in error_rows[0]['errors']
            ),
        )
        self.assertEqual(summary['error_rows'], 1)

    def test_non_numeric_savings_flagged(self):
        """Non-numeric savings_amount values should be rejected."""
        file_obj = NamedStringIO(
            self._csv_with_row(savings_amount='not-a-number'),
            'members.csv',
        )
        rows = parse_member_import_file(file_obj)

        valid_rows, error_rows, summary = validate_import_file(rows)

        self.assertEqual(len(valid_rows), 0)
        self.assertEqual(len(error_rows), 1)
        self.assertTrue(
            any(
                'savings_amount must be numeric' in error
                for error in error_rows[0]['errors']
            ),
        )
        self.assertEqual(summary['error_rows'], 1)
