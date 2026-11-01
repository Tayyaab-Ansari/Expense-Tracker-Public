"""Entry point that turns an incoming WhatsApp message into a reply."""

import logging
import mimetypes

from app.bootstrap import get_or_create_user
from app.db import session_scope
from app.llm.agent import AgentResult, run
from app.voice.transcribe import VoiceError, transcribe
from app.whatsapp.client import (
    download_media,
    mark_as_read,
    send_document,
    send_text,
    upload_media,
)
from app.whatsapp.payload import IncomingMessage

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000
UNSUPPORTED_REPLY = "Abhi sirf text aur voice notes support hain."
ERROR_REPLY = "⚠️ Kuch masla hua, dobara try karein."
NO_SPEECH_REPLY = "🎤 Awaaz samajh nahi aayi, dobara bhejein."
TOO_LONG_REPLY = "🎤 Voice note 2 minute se chhota bhejein."
DEFAULT_MIME = "application/pdf"


async def _send_documents(sender: str, documents: list[dict]) -> None:
    """Upload and send each document produced by a tool."""
    for document in documents:
        filename = document.get("filename") or "report.pdf"
        mime = mimetypes.guess_type(filename)[0] or DEFAULT_MIME
        media_id = await upload_media(document["path"], mime)
        await send_document(sender, media_id, filename, document.get("caption"))


async def _deliver(sender: str, reply: str, documents: list[dict]) -> None:
    """Send the documents when there are any, otherwise the text reply."""
    if documents:
        await _send_documents(sender, documents)
    elif reply:
        await send_text(sender, reply)


async def _ask_agent(msg: IncomingMessage, text: str, source: str) -> AgentResult:
    """Run the agent for one message and return its result."""
    with session_scope() as session:
        user = get_or_create_user(session, msg.sender, msg.contact_name)
        return await run(session, user, text, {"source": source, "raw_text": text})


async def _answer_text(msg: IncomingMessage) -> None:
    """Answer a text message."""
    text = (msg.text or "")[:MAX_TEXT_CHARS]
    result = await _ask_agent(msg, text, "text")
    await _deliver(msg.sender, result.reply, result.documents)


async def _answer_audio(msg: IncomingMessage) -> None:
    """Transcribe a voice note, then answer it quoting the transcript."""
    try:
        audio, _ = await download_media(msg.media_id)
        transcript = (await transcribe(audio))[:MAX_TEXT_CHARS].strip()
    except VoiceError as exc:
        logger.info("Could not use voice note %s: %s", msg.message_id, exc)
        await send_text(msg.sender, TOO_LONG_REPLY if "too long" in str(exc) else NO_SPEECH_REPLY)
        return

    if not transcript:
        await send_text(msg.sender, NO_SPEECH_REPLY)
        return

    result = await _ask_agent(msg, transcript, "voice")
    await _deliver(msg.sender, f"🎤 _{transcript}_\n\n{result.reply}", result.documents)


async def handle_message(msg: IncomingMessage) -> None:
    """Reply to one inbound message, never raising to the caller."""
    try:
        await mark_as_read(msg.message_id)
        if msg.type == "text" and msg.text:
            await _answer_text(msg)
        elif msg.type == "audio" and msg.media_id:
            await _answer_audio(msg)
        else:
            await send_text(msg.sender, UNSUPPORTED_REPLY)
    except Exception:
        logger.exception("Failed to handle message %s", msg.message_id)
        try:
            await send_text(msg.sender, ERROR_REPLY)
        except Exception:
            logger.exception("Could not send the error reply for %s", msg.message_id)
