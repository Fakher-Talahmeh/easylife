"""WhatsApp delivery (eksir API) and phone normalisation.

The API key is read from settings (env) only. It is never logged or echoed in errors.
"""

import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Messages captured by the "locmem" backend (tests).
outbox = []


class WhatsAppDeliveryError(Exception):
    pass


def normalize_phone(raw):
    """Return digits-only international number (e.g. 970591234567) or None if invalid.

    Accepts "+970 59 123 4567", "00970591234567", and local "0591234567"
    (the leading 0 is replaced by DEFAULT_COUNTRY_CODE).
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = settings.DEFAULT_COUNTRY_CODE + digits[1:]
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        return None
    return digits


def send_text(phone, text):
    backend = settings.WHATSAPP_BACKEND
    if backend == "locmem":
        outbox.append({"phone": phone, "text": text})
        return
    if backend == "console":
        # Keep the development backend side-effect free with respect to logs. Phone
        # numbers and OTP contents are sensitive and must never be written to stdout.
        return
    if not settings.WHATSAPP_API_KEY:
        raise WhatsAppDeliveryError("WHATSAPP_API_KEY is not configured")
    try:
        resp = requests.post(
            settings.WHATSAPP_API_URL,
            json={"to": f"{phone}@c.us", "text": text},
            headers={"x-eksir-user-api-key": settings.WHATSAPP_API_KEY},
            timeout=settings.WHATSAPP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("WhatsApp request failed: %s", type(exc).__name__)
        raise WhatsAppDeliveryError("request failed") from exc
    if resp.status_code >= 300:
        logger.warning("WhatsApp API returned HTTP %s", resp.status_code)
        raise WhatsAppDeliveryError(f"HTTP {resp.status_code}")


def send_images_batch(phone, images, caption=""):
    """Send one or more image payloads through the Eksir image endpoint."""
    backend = settings.WHATSAPP_BACKEND
    if backend == "locmem":
        outbox.append({"phone": phone, "caption": caption, "images": images, "type": "images"})
        return
    if backend == "console":
        return
    if not settings.WHATSAPP_API_KEY:
        raise WhatsAppDeliveryError("WHATSAPP_API_KEY is not configured")
    try:
        resp = requests.post(
            settings.WHATSAPP_IMAGES_API_URL,
            json={"to": f"{phone}@c.us", "caption": caption, "images": images},
            headers={"x-eksir-user-api-key": settings.WHATSAPP_API_KEY},
            timeout=settings.WHATSAPP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("WhatsApp image request failed: %s", type(exc).__name__)
        raise WhatsAppDeliveryError("request failed") from exc
    if resp.status_code >= 300:
        logger.warning("WhatsApp image API returned HTTP %s", resp.status_code)
        raise WhatsAppDeliveryError(f"HTTP {resp.status_code}")
