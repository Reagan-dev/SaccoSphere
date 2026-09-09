"""Settings-resolution guards."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.settings.base import _resolve_metropol_mock


class ResolveMetropolMockTests(SimpleTestCase):
    """METROPOL_MOCK must be an explicit, deliberate choice in production."""

    def test_debug_false_and_unset_refuses_to_boot(self):
        with self.assertRaises(ImproperlyConfigured):
            _resolve_metropol_mock(debug=False, raw_value=None)

    def test_debug_false_and_true_refuses_to_boot(self):
        for raw in ('true', 'True', '1', 'yes', 'on'):
            with self.subTest(raw=raw):
                with self.assertRaises(ImproperlyConfigured):
                    _resolve_metropol_mock(debug=False, raw_value=raw)

    def test_debug_false_and_explicit_false_boots_with_mock_off(self):
        for raw in ('false', 'False', '0', 'no', 'off', ''):
            with self.subTest(raw=raw):
                self.assertIs(
                    _resolve_metropol_mock(debug=False, raw_value=raw),
                    False,
                )

    def test_debug_true_defaults_to_mock_on(self):
        self.assertIs(
            _resolve_metropol_mock(debug=True, raw_value=None),
            True,
        )

    def test_debug_true_honours_explicit_false(self):
        self.assertIs(
            _resolve_metropol_mock(debug=True, raw_value='false'),
            False,
        )
