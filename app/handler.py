"""Entry point that turns an incoming WhatsApp message into a reply."""

import logging

from app.whatsapp.client import mark_as_read, send_text
from app.whatsapp.payload import IncomingMessage

logger = logging.getLogger(__name__)

UNSUPPORTED_REPLY = "Abhi sirf text aur voice notes support hain."


async def handle_message(msg: IncomingMessage) -> None:
    """Reply to one inbound message, never raising to the caller."""
    try:
        await mark_as_read(msg.message_id)
        if msg.type == "text" and msg.text:
            await send_text(msg.sender, f"Echo: {msg.text}")
        else:
            await send_text(msg.sender, UNSUPPORTED_REPLY)
    except Exception:
        logger.exception("Failed to handle message %s", msg.message_id)
