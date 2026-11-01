"""Agent loop that drives the LLM through tool calls to a final reply."""

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.llm.client import LLMError, chat
from app.llm.dispatcher import dispatch
from app.llm.prompts import build_system_prompt
from app.llm.tools import TOOLS
from app.models import User
from app.services import accounts as accounts_service
from app.services import categories as categories_service
from app.services import history
from app.utils.dates import today

logger = logging.getLogger(__name__)

MAX_ROUNDS = 5
FALLBACK_REPLY = "⚠️ Samajh nahi aaya, dobara likhein."


@dataclass
class AgentResult:
    """Reply text and any documents the tools produced."""

    reply: str
    documents: list[dict] = field(default_factory=list)


def _system_prompt(session: Session, user: User) -> str:
    """System prompt describing the user's live categories and accounts."""
    account_rows = accounts_service.list_accounts(session, user.id)
    default_account = next(
        (account.name for account in account_rows if account.is_default),
        account_rows[0].name if account_rows else "",
    )
    return build_system_prompt(
        owner_name=user.name,
        today=today(),
        categories_expense=[
            category.name for category in categories_service.list_categories(session, user.id, "expense")
        ],
        categories_income=[
            category.name for category in categories_service.list_categories(session, user.id, "income")
        ],
        accounts=[account.name for account in account_rows],
        default_account=default_account,
    )


def _assistant_message(message) -> dict:
    """Serialise an assistant message with its tool calls for the next request."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in message.tool_calls
        ],
    }


def _parse_arguments(raw: str | None) -> dict | None:
    """Parse tool-call arguments, returning None when they are not usable."""
    if not raw:
        return {}
    try:
        args = json.loads(raw)
    except ValueError:
        return None
    return args if isinstance(args, dict) else None


async def run(
    session: Session, user: User, text: str, context: dict | None = None
) -> AgentResult:
    """Answer one user message, running tool calls until the LLM replies."""
    messages: list[dict] = [{"role": "system", "content": _system_prompt(session, user)}]
    messages.extend(history.get_recent(session, user.id, settings.HISTORY_TURNS))
    messages.append({"role": "user", "content": text})

    reply = FALLBACK_REPLY
    documents: list[dict] = []

    try:
        for _ in range(MAX_ROUNDS):
            message = await chat(messages, tools=TOOLS)

            if not message.tool_calls:
                reply = (message.content or "").strip() or FALLBACK_REPLY
                break

            messages.append(_assistant_message(message))
            for call in message.tool_calls:
                args = _parse_arguments(call.function.arguments)
                if args is None:
                    logger.warning("Tool %s got unparsable arguments", call.function.name)
                    result = {"error": "arguments were not valid JSON"}
                else:
                    result = dispatch(session, user.id, call.function.name, args, context)
                if isinstance(result.get("document"), dict):
                    documents.append(result["document"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        else:
            logger.warning("Agent gave up after %d rounds", MAX_ROUNDS)
    except LLMError:
        logger.exception("Agent could not reach the LLM")

    history.save_turn(session, user.id, "user", text)
    history.save_turn(session, user.id, "assistant", reply)
    session.commit()

    return AgentResult(reply=reply, documents=documents)
