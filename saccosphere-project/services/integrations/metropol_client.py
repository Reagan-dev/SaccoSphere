# NOTE: production Metropol/TransUnion access requires a signed data-sharing agreement and compliance with the Credit Reference Bureau Regulations and the Data Protection Act, 2019. Only the minimum data needed for the check should ever be sent.
import hashlib
import logging

import requests
from django.conf import settings


logger = logging.getLogger('saccosphere.metropol')


class CRBCheckError(Exception):
    """Raised when the CRB check service cannot be reached."""

    pass


class MetropolClient:
    """Client for checking credit status through Metropol/TransUnion CRB."""

    # Credit band constants
    POOR = 'POOR'
    FAIR = 'FAIR'
    GOOD = 'GOOD'
    VERY_GOOD = 'VERY_GOOD'
    EXCELLENT = 'EXCELLENT'

    def __init__(self):
        self.api_key = settings.METROPOL_API_KEY
        self.api_url = settings.METROPOL_API_URL
        self.mock = settings.DEBUG or settings.METROPOL_MOCK

    def check_credit(self, id_number, phone_number=None):
        """
        Check credit status for a given ID number and return standard response dict.
        """
        if self.mock:
            return self._mock_response(id_number)

        payload = {'id_number': id_number}
        if phone_number:
            payload['phone_number'] = phone_number

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception('Metropol CRB request failed.')
            raise CRBCheckError('CRB check request failed.') from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise CRBCheckError('CRB returned invalid JSON.') from exc

        return self._standardize_response(data, id_number)

    def _mock_response(self, id_number):
        """
        Generate deterministic mock response based on ID number hash.
        Same ID always returns same result for test consistency.
        """
        # Create hash of ID number for deterministic results
        hash_value = int(hashlib.md5(id_number.encode()).hexdigest(), 16)
        
        # Score range: 300-850 (standard credit score range)
        score = 300 + (hash_value % 551)
        
        # Determine band based on score
        if score < 500:
            band = self.POOR
        elif score < 600:
            band = self.FAIR
        elif score < 700:
            band = self.GOOD
        elif score < 750:
            band = self.VERY_GOOD
        else:
            band = self.EXCELLENT
        
        # Listed negative: 20% chance based on hash
        listed_negative = (hash_value % 5) == 0

        return {
            'checked': True,
            'score': score,
            'band': band,
            'listed_negative': listed_negative,
            'reference': f'MOCK-CRB-{id_number}',
            'provider': 'metropol',
        }

    def _standardize_response(self, data, id_number):
        """Standardize CRB provider response into consistent format."""
        score = data.get('score') or data.get('credit_score')
        band = data.get('band') or data.get('credit_band') or data.get('rating')
        
        # Map various band names to our standard bands
        band_mapping = {
            'poor': self.POOR,
            'fair': self.FAIR,
            'good': self.GOOD,
            'very good': self.VERY_GOOD,
            'very_good': self.VERY_GOOD,
            'excellent': self.EXCELLENT,
        }
        
        if band and band.lower() in band_mapping:
            band = band_mapping[band.lower()]
        elif score:
            # Derive band from score if not provided
            if score < 500:
                band = self.POOR
            elif score < 600:
                band = self.FAIR
            elif score < 700:
                band = self.GOOD
            elif score < 750:
                band = self.VERY_GOOD
            else:
                band = self.EXCELLENT
        else:
            band = self.FAIR  # Default fallback

        listed_negative = bool(
            data.get('listed_negative')
            or data.get('negative_list')
            or data.get('blacklisted')
        )

        return {
            'checked': True,
            'score': score,
            'band': band,
            'listed_negative': listed_negative,
            'reference': (
                data.get('reference')
                or data.get('request_id')
                or data.get('transaction_id')
                or f'METROPOL-{id_number}'
            ),
            'provider': 'metropol',
        }
