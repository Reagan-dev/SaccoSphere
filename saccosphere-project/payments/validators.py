from config.utils import InvalidPhoneNumberError, normalize_phone_number
from rest_framework import serializers


def validate_mpesa_phone(phone_number):
    """Validate and normalize a phone number for M-Pesa transactions.
    
    This function validates that the phone number is a valid Kenyan mobile number
    and returns it in canonical E.164 format (+254712345678).
    
    Args:
        phone_number: Phone number in any accepted format
    
    Returns:
        str: Phone number in E.164 format (+254712345678)
    
    Raises:
        serializers.ValidationError: If the phone number is invalid
    """
    try:
        return normalize_phone_number(phone_number)
    except InvalidPhoneNumberError as exc:
        raise serializers.ValidationError(str(exc))
