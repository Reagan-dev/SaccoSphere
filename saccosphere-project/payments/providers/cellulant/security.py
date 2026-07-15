from __future__ import annotations

import hmac
import logging
from hashlib import sha256
from time import time

from django.conf import settings


logger = logging.getLogger(__name__)


class CellulantWebhookVerifier:
    """Validate Cellulant webhook signatures and replay protection."""

    SIGNATURE_HEADER = "X-Cellulant-Signature"
    TIMESTAMP_HEADER = "X-Cellulant-Timestamp"
    REPLAY_WINDOW_SECONDS = 300

    def verify(self, request) -> bool:
        """Validate an incoming webhook request using HMAC and timestamp checks."""
        received_sig = request.headers.get(self.SIGNATURE_HEADER, "")
        timestamp = request.headers.get(self.TIMESTAMP_HEADER, "")

        if not received_sig or not timestamp:
            logger.warning("Cellulant webhook missing signature or timestamp headers")
            return False

        try:
            timestamp_value = int(timestamp)
        except ValueError:
            logger.warning("Cellulant webhook timestamp is not an integer")
            return False

        if abs(int(time()) - timestamp_value) > self.REPLAY_WINDOW_SECONDS:
            logger.warning("Cellulant webhook timestamp is outside the replay window")
            return False

        body_bytes = request.body or b""
        message = f"{timestamp}.".encode("utf-8") + body_bytes
        expected_sig = hmac.new(
            settings.CELLULANT_SECRET_KEY.encode("utf-8"),
            message,
            sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, received_sig):
            logger.warning("Cellulant webhook signature did not match")
            return False

        return True
