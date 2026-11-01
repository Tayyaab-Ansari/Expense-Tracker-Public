"""FastAPI router exposing the WhatsApp webhook verify and receive endpoints."""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import session_scope
from app.handler import handle_message
from app.models import ProcessedMessage
from app.whatsapp.payload import IncomingMessage, parse_webhook
from app.whatsapp.security import is_allowed, verify_signature

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])


def _already_processed(msg: IncomingMessage) -> bool:
    """Record a message id, reporting True when it was already handled."""
    try:
        with session_scope() as session:
            session.add(
                ProcessedMessage(wa_message_id=msg.message_id, user_phone=msg.sender)
            )
            session.flush()
    except IntegrityError:
        return True
    return False


@router.get("/webhook", response_class=PlainTextResponse)
def verify(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> str:
    """Answer Meta's subscription challenge when the verify token matches."""
    if mode != "subscribe" or token != settings.WA_VERIFY_TOKEN or challenge is None:
        logger.warning("Rejected webhook verification request")
        raise HTTPException(status_code=403, detail="Verification failed")
    return challenge


@router.post("/webhook")
async def receive(request: Request, background_tasks: BackgroundTasks) -> dict[str, bool]:
    """Accept an inbound webhook and queue each allowed, unseen message for handling."""
    raw_body = await request.body()
    if not verify_signature(raw_body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except ValueError:
        logger.warning("Webhook body was not valid JSON")
        return {"ok": True}

    for msg in parse_webhook(payload):
        if not is_allowed(msg.sender):
            logger.info("Ignored stranger ****%s", msg.sender[-4:])
            continue
        if _already_processed(msg):
            logger.info("Skipped duplicate message %s", msg.message_id)
            continue
        background_tasks.add_task(handle_message, msg)

    return {"ok": True}
