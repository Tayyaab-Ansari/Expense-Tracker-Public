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

# tools whose confirmation_text is the whole answer, so no second LLM call is needed
SHORT_CIRCUIT_TOOLS = frozenset(
    {
        "add_transactions",
        "update_transaction",
        "delete_transaction",
        "undo_last",
        "set_account_balance",
        "set_default_account",
    }
)


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
    """Serialise an assistant message, keeping tool calls and reasoning details intact."""
    return message.model_dump(exclude_none=True)


def _short_circuit_reply(results: list[tuple[str, dict]]) -> str | None:
    """Confirmation lines for a round that already answered the user, else None."""
    if not results:
        return None
    if any(
        name not in SHORT_CIRCUIT_TOOLS or result.get("error") or not result.get("confirmation_text")
        for name, result in results
    ):
        return None

    lines: list[str] = []
    for _, result in results:
        lines.append(result["confirmation_text"])
        lines.extend(result.get("budget_warnings") or [])
    return "\n".join(lines)


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
            results: list[tuple[str, dict]] = []
            for call in message.tool_calls:
                name = call.function.name
                args = _parse_arguments(call.function.arguments)
                if args is None:
                    logger.warning("Tool %s got unparsable arguments", name)
                    result = {"error": "arguments were not valid JSON"}
                else:
                    result = dispatch(session, user.id, name, args, context)
                if isinstance(result.get("document"), dict):
                    documents.append(result["document"])
                results.append((name, result))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            confirmed = _short_circuit_reply(results)
            if confirmed:
                reply = confirmed
                break
        else:
            logger.warning("Agent gave up after %d rounds", MAX_ROUNDS)
    except LLMError:
        logger.exception("Agent could not reach the LLM")

    history.save_turn(session, user.id, "user", text)
    history.save_turn(session, user.id, "assistant", reply)
    session.commit()

    return AgentResult(reply=reply, documents=documents)
