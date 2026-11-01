"""Tests for voice note conversion and transcription."""

import asyncio
import base64
import shutil

import pytest
from openai.types.chat import ChatCompletion

from app.llm import client as llm_client
from app.voice import transcribe as voice

pytestmark = pytest.mark.asyncio(loop_scope="function")

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


async def sine_wav() -> bytes:
    """One second of audio, produced by ffmpeg itself."""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-f",
        "wav",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    data, _ = await process.communicate()
    return data


@pytest.fixture
def transcription(monkeypatch: pytest.MonkeyPatch):
    """Stub the LLM call and record the request it received."""

    def install(content: str | None):
        captured: dict = {}
        completion = ChatCompletion.model_validate(
            {
                "id": "1",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

        class Completions:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return completion

        class Client:
            class chat:  # noqa: N801 - mirrors the SDK's attribute layout
                completions = Completions()

        monkeypatch.setattr(llm_client, "get_client", lambda: Client())
        return captured

    return install


async def test_audio_over_the_limit_is_rejected() -> None:
    with pytest.raises(voice.VoiceError, match="too long"):
        await voice.convert_to_mp3(b"x" * (voice.MAX_AUDIO_BYTES + 1))


async def test_empty_audio_is_rejected() -> None:
    with pytest.raises(voice.VoiceError, match="empty audio"):
        await voice.convert_to_mp3(b"")


async def test_missing_ffmpeg_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)

    with pytest.raises(voice.VoiceError, match="ffmpeg not installed"):
        await voice.convert_to_mp3(b"audio bytes")


@needs_ffmpeg
async def test_convert_to_mp3_produces_audio() -> None:
    mp3 = await voice.convert_to_mp3(await sine_wav())

    assert mp3
    assert mp3[:3] in (b"ID3", b"\xff\xfb")


@needs_ffmpeg
async def test_unconvertible_audio_is_reported() -> None:
    with pytest.raises(voice.VoiceError, match="could not convert"):
        await voice.convert_to_mp3(b"this is not audio")


@needs_ffmpeg
async def test_transcribe_sends_prompt_and_audio(transcription) -> None:
    captured = transcription("  chai pe 150 kharch hue  ")

    text = await voice.transcribe(await sine_wav())

    assert text == "chai pe 150 kharch hue"
    assert captured["model"] == "google/gemini-2.5-flash-lite"

    content = captured["messages"][0]["content"]
    assert [part["type"] for part in content] == ["text", "input_audio"]
    assert content[0]["text"] == voice.PROMPT
    assert content[1]["input_audio"]["format"] == "mp3"
    assert base64.b64decode(content[1]["input_audio"]["data"])[:3] in (b"ID3", b"\xff\xfb")


@needs_ffmpeg
async def test_blank_transcript_surfaces_as_an_llm_error(transcription) -> None:
    transcription("   ")

    with pytest.raises(llm_client.LLMError):
        await voice.transcribe(await sine_wav())
