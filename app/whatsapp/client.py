"""Async WhatsApp Cloud API client for sending messages and fetching media."""

import logging
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = f"https://graph.facebook.com/{settings.GRAPH_API_VERSION}"
TIMEOUT = 30.0
MAX_BODY_CHARS = 4000


class WhatsAppError(Exception):
    """Raised when the WhatsApp Cloud API returns an error."""


def _client() -> httpx.AsyncClient:
    """Async client pointed at the Graph API with the access token attached."""
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {settings.WA_ACCESS_TOKEN.get_secret_value()}"},
    )


def _check(response: httpx.Response, action: str) -> dict:
    """Return the JSON body, raising WhatsAppError on a non-2xx response."""
    if response.is_success:
        return response.json()

    try:
        message = response.json().get("error", {}).get("message", "")
    except ValueError:
        message = ""
    logger.error("WhatsApp %s failed with status %s: %s", action, response.status_code, message)
    raise WhatsAppError(f"{action} failed with status {response.status_code}: {message}")


def _split_body(body: str) -> list[str]:
    """Split a long body into message-sized chunks, preferring line breaks."""
    if len(body) <= MAX_BODY_CHARS:
        return [body]

    chunks: list[str] = []
    current = ""
    for line in body.splitlines(keepends=True):
        while len(line) > MAX_BODY_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:MAX_BODY_CHARS])
            line = line[MAX_BODY_CHARS:]
        if len(current) + len(line) > MAX_BODY_CHARS:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return [chunk.strip("\n") for chunk in chunks if chunk.strip()]


async def send_text(to: str, body: str) -> list[str]:
    """Send a text message, splitting bodies that exceed the message limit."""
    message_ids: list[str] = []
    async with _client() as client:
        for chunk in _split_body(body):
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            }
            response = await client.post(f"/{settings.WA_PHONE_NUMBER_ID}/messages", json=payload)
            data = _check(response, "send_text")
            message_ids.extend(message["id"] for message in data.get("messages", []))
    return message_ids


async def mark_as_read(message_id: str) -> None:
    """Mark an incoming message as read, ignoring failures."""
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    try:
        async with _client() as client:
            response = await client.post(f"/{settings.WA_PHONE_NUMBER_ID}/messages", json=payload)
            _check(response, "mark_as_read")
    except (WhatsAppError, httpx.HTTPError) as exc:
        logger.warning("Could not mark message as read: %s", exc)


async def download_media(media_id: str) -> tuple[bytes, str]:
    """Download a media file by id, returning its bytes and mime type."""
    async with _client() as client:
        metadata = _check(await client.get(f"/{media_id}"), "download_media")
        url = metadata.get("url")
        if not url:
            raise WhatsAppError(f"No download url for media {media_id}")
        response = await client.get(url)
        if not response.is_success:
            _check(response, "download_media")
        return response.content, metadata.get("mime_type", "application/octet-stream")


async def upload_media(path: str | Path, mime: str) -> str:
    """Upload a local file and return its media id."""
    file_path = Path(path)
    files = {"file": (file_path.name, file_path.read_bytes(), mime)}
    data = {"type": mime, "messaging_product": "whatsapp"}
    async with _client() as client:
        response = await client.post(f"/{settings.WA_PHONE_NUMBER_ID}/media", data=data, files=files)
        return _check(response, "upload_media")["id"]


async def send_document(
    to: str, media_id: str, filename: str, caption: str | None = None
) -> list[str]:
    """Send a previously uploaded document."""
    document: dict = {"id": media_id, "filename": filename}
    if caption:
        document["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "document",
        "document": document,
    }
    async with _client() as client:
        response = await client.post(f"/{settings.WA_PHONE_NUMBER_ID}/messages", json=payload)
        data = _check(response, "send_document")
    return [message["id"] for message in data.get("messages", [])]
