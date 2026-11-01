"""Entry point that turns an incoming WhatsApp message into a reply."""

import logging
import mimetypes

from app.bootstrap import get_or_create_user
from app.db import session_scope
from app.llm.agent import run
from app.whatsapp.client import mark_as_read, send_document, send_text, upload_media
from app.whatsapp.payload import IncomingMessage

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000
UNSUPPORTED_REPLY = "Abhi sirf text aur voice notes support hain."
ERROR_REPLY = "⚠️ Kuch masla hua, dobara try karein."
DEFAULT_MIME = "application/pdf"


async def _send_documents(sender: str, documents: list[dict]) -> None:
    """Upload and send each document produced by a tool."""
    for document in documents:
        filename = document.get("filename") or "report.pdf"
        mime = mimetypes.guess_type(filename)[0] or DEFAULT_MIME
        media_id = await upload_media(document["path"], mime)
        await send_document(sender, media_id, filename, document.get("caption"))


async def _answer(msg: IncomingMessage) -> None:
    """Run the agent for one text message and send what it produced."""
    text = (msg.text or "")[:MAX_TEXT_CHARS]
    with session_scope() as session:
        user = get_or_create_user(session, msg.sender, msg.contact_name)
        result = await run(session, user, text, {"source": "text", "raw_text": text})

    if result.documents:
        await _send_documents(msg.sender, result.documents)
    elif result.reply:
        await send_text(msg.sender, result.reply)


async def handle_message(msg: IncomingMessage) -> None:
    """Reply to one inbound message, never raising to the caller."""
    try:
        await mark_as_read(msg.message_id)
        if msg.type == "text" and msg.text:
            await _answer(msg)
        elif msg.type == "audio":
            # TODO(P31): transcribe the voice note and run it through the agent
            await send_text(msg.sender, UNSUPPORTED_REPLY)
        else:
            await send_text(msg.sender, UNSUPPORTED_REPLY)
    except Exception:
        logger.exception("Failed to handle message %s", msg.message_id)
        try:
            await send_text(msg.sender, ERROR_REPLY)
        except Exception:
            logger.exception("Could not send the error reply for %s", msg.message_id)
