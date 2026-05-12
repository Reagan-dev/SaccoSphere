import re

from rest_framework import serializers


MPESA_PHONE_REGEX = re.compile(r'^(?:\+?254|0)(?:7|1)\d{8}$')


def validate_mpesa_phone(phone_number):
    if not MPESA_PHONE_REGEX.match(str(phone_number)):
        raise serializers.ValidationError(
            'Phone number must be a valid Kenyan M-Pesa number, for example '
            '+254712345678, 254712345678, or 0712345678.'
        )

    digits = re.sub(r'\D', '', str(phone_number))
    if digits.startswith('0'):
        return f'254{digits[1:]}'

    return digits
