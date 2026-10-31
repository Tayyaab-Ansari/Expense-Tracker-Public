"""Parsing of WhatsApp webhook payloads into incoming message objects."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    """One inbound WhatsApp message in the shape the handler needs."""

    message_id: str
    sender: str
    type: str
    text: str | None = None
    media_id: str | None = None
    mime_type: str | None = None
    timestamp: int = 0
    contact_name: str | None = None


def _digits(value: str) -> str:
    """Keep only the digits of a phone number."""
    return "".join(char for char in value if char.isdigit())


def _contact_name(value: dict) -> str | None:
    """Profile name of the first contact in a change value, when present."""
    contacts = value.get("contacts")
    if not isinstance(contacts, list) or not contacts:
        return None
    profile = contacts[0].get("profile") if isinstance(contacts[0], dict) else None
    if not isinstance(profile, dict):
        return None
    name = profile.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _parse_message(message: dict, contact_name: str | None) -> IncomingMessage | None:
    """Convert one raw message object, or None when it cannot be understood."""
    message_id = message.get("id")
    sender = message.get("from")
    if not message_id or not sender:
        logger.warning("Skipping webhook message without id or sender")
        return None

    try:
        timestamp = int(message.get("timestamp", 0))
    except (TypeError, ValueError):
        timestamp = 0

    kind = message.get("type")
    text: str | None = None
    media_id: str | None = None
    mime_type: str | None = None

    if kind == "text":
        body = message.get("text", {}).get("body") if isinstance(message.get("text"), dict) else None
        text = body if isinstance(body, str) else None
        kind = "text" if text is not None else "unsupported"
    elif kind in ("audio", "voice"):
        audio = message.get(kind)
        if isinstance(audio, dict) and audio.get("id"):
            media_id = audio["id"]
            mime_type = audio.get("mime_type")
            kind = "audio"
        else:
            kind = "unsupported"
    else:
        logger.info("Unsupported incoming message type: %s", kind)
        kind = "unsupported"

    return IncomingMessage(
        message_id=message_id,
        sender=_digits(sender),
        type=kind,
        text=text,
        media_id=media_id,
        mime_type=mime_type,
        timestamp=timestamp,
        contact_name=contact_name,
    )


def parse_webhook(body: dict) -> list[IncomingMessage]:
    """Extract every inbound message from a webhook payload, skipping what cannot be parsed."""
    messages: list[IncomingMessage] = []
    if not isinstance(body, dict):
        logger.warning("Ignoring webhook payload that is not an object")
        return messages

    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue

            raw_messages = value.get("messages")
            if not isinstance(raw_messages, list):
                continue

            contact_name = _contact_name(value)
            for raw in raw_messages:
                if not isinstance(raw, dict):
                    continue
                try:
                    parsed = _parse_message(raw, contact_name)
                except Exception:
                    logger.exception("Failed to parse a webhook message")
                    continue
                if parsed is not None:
                    messages.append(parsed)

    return messages
