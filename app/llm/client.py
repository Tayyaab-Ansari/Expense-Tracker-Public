"""OpenRouter chat completion client with fallback model support."""

import logging
from functools import lru_cache

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
TIMEOUT = 30.0
TEMPERATURE = 0.2


class LLMError(Exception):
    """Raised when the LLM could not be reached on either model."""


@lru_cache
def get_client() -> AsyncOpenAI:
    """Cached OpenRouter client."""
    return AsyncOpenAI(
        base_url=BASE_URL,
        api_key=settings.OPENROUTER_API_KEY.get_secret_value(),
        timeout=TIMEOUT,
        default_headers={"X-Title": "WhatsApp Expense Bot"},
    )


def _log_usage(model: str, completion: ChatCompletion) -> None:
    """Log the model used and its token usage."""
    usage = completion.usage
    logger.info(
        "LLM %s used %s prompt and %s completion tokens",
        model,
        usage.prompt_tokens if usage else "unknown",
        usage.completion_tokens if usage else "unknown",
    )


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
    client = get_client()

    try:
        completion = await client.chat.completions.create(model=primary, **kwargs)
        _log_usage(primary, completion)
        return completion.choices[0].message
    except Exception as exc:
        logger.warning("LLM %s failed (%s), retrying with %s", primary, exc, fallback or "nothing")
        if not fallback or fallback == primary:
            raise LLMError(f"LLM request failed on {primary}: {exc}") from exc

    try:
        completion = await client.chat.completions.create(model=fallback, **kwargs)
        _log_usage(fallback, completion)
        return completion.choices[0].message
    except Exception as exc:
        logger.error("LLM fallback %s also failed: %s", fallback, exc)
        raise LLMError(f"LLM request failed on {primary} and {fallback}: {exc}") from exc
