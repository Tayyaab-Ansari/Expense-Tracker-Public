"""Transcription of WhatsApp voice notes via ffmpeg and the transcription model."""

import asyncio
import base64
import logging

from app.config import settings
from app.llm.client import chat

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 3 * 1024 * 1024
FFMPEG_ARGS = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-i",
    "pipe:0",
    "-ac",
    "1",
    "-ar",
    "16000",
    "-b:a",
    "32k",
    "-f",
    "mp3",
    "pipe:1",
)
PROMPT = (
    "Transcribe this voice note exactly. If it is Urdu, write it in Roman Urdu. "
    "Output only the transcript, nothing else."
)


class VoiceError(Exception):
    """Raised when a voice note cannot be converted or transcribed."""


async def convert_to_mp3(data: bytes) -> bytes:
    """Convert audio bytes to a small mono mp3 with ffmpeg."""
    if not data:
        raise VoiceError("empty audio")
    if len(data) > MAX_AUDIO_BYTES:
        raise VoiceError("too long")

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *FFMPEG_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VoiceError("ffmpeg not installed") from exc

    mp3, errors = await process.communicate(data)
    if process.returncode != 0 or not mp3:
        logger.error("ffmpeg failed with code %s: %s", process.returncode, errors.decode()[:200])
        raise VoiceError("could not convert the voice note")
    return mp3


async def transcribe(data: bytes) -> str:
    """Transcribe a voice note, returning an empty string when nothing was heard."""
    mp3 = await convert_to_mp3(data)
    encoded = base64.b64encode(mp3).decode()

    message = await chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": encoded, "format": "mp3"},
                    },
                ],
            }
        ],
        model=settings.TRANSCRIBE_MODEL,
    )
    return (message.content or "").strip()
