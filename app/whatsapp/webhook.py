"""FastAPI router exposing the WhatsApp webhook verify and receive endpoints."""

import logging

from fastapi import APIRouter, Body, HTTPException, Query

from app.config import settings
from app.whatsapp.payload import parse_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["whatsapp"])


@router.get("/")
def verify(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> int:
    """Answer Meta's subscription challenge when the verify token matches."""
    if mode != "subscribe" or token != settings.WA_VERIFY_TOKEN or challenge is None:
        logger.warning("Rejected webhook verification request")
        raise HTTPException(status_code=403, detail="Verification failed")
    try:
        return int(challenge)
    except ValueError as exc:
        logger.warning("Webhook verification challenge was not a number")
        raise HTTPException(status_code=403, detail="Verification failed") from exc


@router.post("/")
def receive(payload: dict = Body(...)) -> dict[str, str]:
    """Accept an inbound webhook payload and log the messages it carries."""
    messages = parse_webhook(payload)
    for message in messages:
        logger.info(
            "Incoming %s message %s from %s", message.type, message.message_id, message.sender
        )
    if not messages:
        logger.debug("Webhook payload carried no messages")
    return {"status": "ok"}
