"""OpenRouter chat completion client with fallback model support."""

import asyncio
import logging

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
TIMEOUT = 30.0
TEMPERATURE = 0.2
RAW_LOG_CHARS = 1000

_client: AsyncOpenAI | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


class LLMError(Exception):
    """Raised when the LLM could not be reached on either model."""


class _UnusableResponse(Exception):
    """Raised when a completion carries no answer the agent can use."""


def get_client() -> AsyncOpenAI:
    """OpenRouter client for the running event loop, rebuilt when the loop changes."""
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _client is None or (loop is not None and loop is not _client_loop):
        _client = AsyncOpenAI(
            base_url=BASE_URL,
            api_key=settings.OPENROUTER_API_KEY.get_secret_value(),
            timeout=TIMEOUT,
            default_headers={"X-Title": "WhatsApp Expense Bot"},
        )
        _client_loop = loop
    return _client


def _log_usage(model: str, completion: ChatCompletion) -> None:
    """Log the model used and its token usage."""
    usage = completion.usage
    logger.info(
        "LLM %s used %s prompt and %s completion tokens",
        model,
        usage.prompt_tokens if usage else "unknown",
        usage.completion_tokens if usage else "unknown",
    )


def _raw(completion: ChatCompletion) -> str:
    """First part of a raw completion, for diagnosing an unusable response."""
    try:
        return completion.model_dump_json()[:RAW_LOG_CHARS]
    except Exception:
        return repr(completion)[:RAW_LOG_CHARS]


def _message_of(model: str, completion: ChatCompletion) -> ChatCompletionMessage:
    """Return the usable message of a completion, rejecting empty or error responses."""
    error = (completion.model_extra or {}).get("error") if completion else None
    if error:
        logger.warning("LLM %s returned an error: %s", model, _raw(completion))
        raise _UnusableResponse(str(error))
    if not completion.choices:
        logger.warning("LLM %s returned no choices: %s", model, _raw(completion))
        raise _UnusableResponse("no choices")

    message = completion.choices[0].message
    if not message.tool_calls and not (message.content or "").strip():
        logger.warning("LLM %s returned an empty message: %s", model, _raw(completion))
        raise _UnusableResponse("empty content")
    return message


async def _attempt(model: str, kwargs: dict) -> ChatCompletionMessage:
    """Make one completion request and validate what came back."""
    completion = await get_client().chat.completions.create(model=model, **kwargs)
    _log_usage(model, completion)
    return _message_of(model, completion)


async def chat(
    messages: list[dict],
    tools: list | None = None,
    model: str | None = None,
    temperature: float = TEMPERATURE,
) -> ChatCompletionMessage:
    """Request one completion, retrying once on the fallback model."""
    kwargs: dict = {"messages": messages, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    primary = model or settings.LLM_MODEL
    fallback = settings.LLM_FALLBACK_MODEL

    try:
        return await _attempt(primary, kwargs)
    except Exception as exc:
        logger.warning("LLM %s failed (%s), retrying with %s", primary, exc, fallback or "nothing")
        if not fallback or fallback == primary:
            raise LLMError(f"LLM request failed on {primary}: {exc}") from exc

    try:
        return await _attempt(fallback, kwargs)
    except Exception as exc:
        logger.error("LLM fallback %s also failed: %s", fallback, exc)
        raise LLMError(f"LLM request failed on {primary} and {fallback}: {exc}") from exc
