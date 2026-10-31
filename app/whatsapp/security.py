"""Webhook verification and request signature validation."""

import hashlib
import hmac
import logging

from app.config import settings

logger = logging.getLogger(__name__)

SIGNATURE_PREFIX = "sha256="

_warned_missing_secret = False


def _warn_missing_secret_once() -> None:
    """Log the unsigned-webhook warning only the first time it happens."""
    global _warned_missing_secret
    if not _warned_missing_secret:
        _warned_missing_secret = True
        logger.warning("WA_APP_SECRET is not set; webhook signatures are not being verified")


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Check the X-Hub-Signature-256 header against the raw request body."""
    secret = settings.WA_APP_SECRET.get_secret_value()
    if not secret:
        _warn_missing_secret_once()
        return settings.APP_ENV.lower() == "development"

    if not header or not header.startswith(SIGNATURE_PREFIX):
        logger.warning("Webhook request has a missing or malformed signature header")
        return False

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header[len(SIGNATURE_PREFIX) :]):
        logger.warning("Webhook signature did not match the request body")
        return False
    return True


def is_allowed(sender: str) -> bool:
    """Whether a sender's phone number may use the bot."""
    digits = "".join(char for char in sender if char.isdigit())
    return digits in settings.allowed_numbers
