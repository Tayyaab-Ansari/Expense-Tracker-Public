"""Tests for the agent loop and the LLM client's response handling."""

import json

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from sqlalchemy.orm import Session

from app.llm import agent
from app.llm import client as llm_client
from app.models import User
from app.services import history
from app.services import transactions as tx

pytestmark = pytest.mark.asyncio(loop_scope="function")


def tool_message(*calls: tuple[str, dict]) -> ChatCompletionMessage:
    """Assistant message asking for one or more tool calls."""
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ChatCompletionMessageToolCall(
                id=f"call-{index}",
                type="function",
                function=Function(name=name, arguments=json.dumps(args)),
            )
            for index, (name, args) in enumerate(calls)
        ],
    )


def text_message(content: str | None) -> ChatCompletionMessage:
    """Assistant message with a final answer."""
    return ChatCompletionMessage(role="assistant", content=content)


def completion(message: dict | None = None, error: dict | None = None) -> ChatCompletion:
    """Build a raw completion, optionally empty or carrying a provider error."""
    data: dict = {"id": "1", "object": "chat.completion", "created": 1, "model": "m", "choices": []}
    if message is not None:
        data["choices"] = [{"index": 0, "message": message, "finish_reason": "stop"}]
    if error is not None:
        data["error"] = error
    return ChatCompletion.model_validate(data)


@pytest.fixture
def fake_chat(monkeypatch: pytest.MonkeyPatch):
    """Replace agent.chat with a scripted sequence, recording the messages sent."""

    def install(*outcomes):
        sent: list[list[dict]] = []
        queue = list(outcomes)

        async def chat(messages, tools=None, model=None, temperature=0.2):
            sent.append([dict(message) for message in messages])
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(agent, "chat", chat)
        return sent

    return install


async def test_saving_short_circuits_without_a_second_call(
    session: Session, user: User, fake_chat
) -> None:
    sent = fake_chat(
        tool_message(("add_transactions", {"items": [{"amount": 150, "category": "food"}]}))
    )

    result = await agent.run(session, user, "chai 150")

    assert len(sent) == 1
    assert result.reply.startswith("✅")
    assert "Food" in result.reply and "Rs 150" in result.reply
    assert len(tx.recent(session, user.id)) == 1


async def test_short_circuit_joins_every_confirmation(
    session: Session, user: User, fake_chat
) -> None:
    fake_chat(
        tool_message(
            ("add_transactions", {"items": [{"amount": 150, "category": "food"}]}),
            ("add_transactions", {"items": [{"amount": 200, "category": "transport"}]}),
        )
    )

    result = await agent.run(session, user, "chai 150, rickshaw 200")

    assert len(result.reply.splitlines()) == 2
    assert len(tx.recent(session, user.id)) == 2


async def test_short_circuit_records_history(session: Session, user: User, fake_chat) -> None:
    fake_chat(tool_message(("add_transactions", {"items": [{"amount": 150, "category": "food"}]})))

    result = await agent.run(session, user, "chai 150")

    assert history.get_recent(session, user.id, 1) == [
        {"role": "user", "content": "chai 150"},
        {"role": "assistant", "content": result.reply},
    ]


async def test_failed_tool_does_not_short_circuit(session: Session, user: User, fake_chat) -> None:
    sent = fake_chat(
        tool_message(("add_transactions", {"items": [{"amount": 0, "category": "food"}]})),
        text_message("Amount theek nahi tha."),
    )

    result = await agent.run(session, user, "chai 0")

    assert len(sent) == 2
    assert result.reply == "Amount theek nahi tha."
    assert json.loads(sent[1][-1]["content"])["error"]


async def test_read_only_tool_does_not_short_circuit(
    session: Session, user: User, fake_chat
) -> None:
    sent = fake_chat(
        tool_message(("get_balances", {})),
        text_message("Cash: Rs 0"),
    )

    result = await agent.run(session, user, "balance?")

    assert len(sent) == 2
    assert result.reply == "Cash: Rs 0"


async def test_mixed_round_does_not_short_circuit(session: Session, user: User, fake_chat) -> None:
    sent = fake_chat(
        tool_message(
            ("add_transactions", {"items": [{"amount": 150, "category": "food"}]}),
            ("get_balances", {}),
        ),
        text_message("Done, Cash: Rs -150"),
    )

    await agent.run(session, user, "chai 150 aur balance batao")

    assert len(sent) == 2


async def test_tool_messages_carry_id_name_and_string_content(
    session: Session, user: User, fake_chat
) -> None:
    sent = fake_chat(
        tool_message(("get_balances", {})),
        text_message("Cash: Rs 0"),
    )

    await agent.run(session, user, "balance?")

    tool_msg = sent[1][-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call-0"
    assert tool_msg["name"] == "get_balances"
    assert isinstance(tool_msg["content"], str)

    assistant_msg = sent[1][-2]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_balances"
    assert "content" not in assistant_msg or assistant_msg["content"] is not None


async def test_llm_error_falls_back_to_the_canned_reply(
    session: Session, user: User, fake_chat
) -> None:
    fake_chat(llm_client.LLMError("down"))

    result = await agent.run(session, user, "kuch bhi")

    assert result.reply == agent.FALLBACK_REPLY
    assert history.get_recent(session, user.id, 1)[0]["content"] == "kuch bhi"


@pytest.fixture
def fake_create(monkeypatch: pytest.MonkeyPatch):
    """Replace the OpenAI SDK call with a scripted sequence of raw completions."""

    def install(*outcomes):
        models: list[str] = []
        queue = list(outcomes)

        class Completions:
            async def create(self, **kwargs):
                models.append(kwargs["model"])
                outcome = queue.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        class Client:
            class chat:  # noqa: N801 - mirrors the SDK's attribute layout
                completions = Completions()

        monkeypatch.setattr(llm_client, "get_client", lambda: Client())
        return models

    return install


async def test_empty_response_retries_on_the_fallback_model(fake_create) -> None:
    models = fake_create(
        completion({"role": "assistant", "content": ""}),
        completion({"role": "assistant", "content": "theek hai"}),
    )

    message = await llm_client.chat([{"role": "user", "content": "hi"}])

    assert message.content == "theek hai"
    assert len(models) == 2


async def test_response_without_choices_retries(fake_create) -> None:
    models = fake_create(
        completion(),
        completion({"role": "assistant", "content": "theek hai"}),
    )

    assert (await llm_client.chat([{"role": "user", "content": "hi"}])).content == "theek hai"
    assert len(models) == 2


async def test_provider_error_field_retries(fake_create) -> None:
    models = fake_create(
        completion({"role": "assistant", "content": "ignored"}, error={"message": "rate limited"}),
        completion({"role": "assistant", "content": "theek hai"}),
    )

    assert (await llm_client.chat([{"role": "user", "content": "hi"}])).content == "theek hai"
    assert len(models) == 2


async def test_two_empty_responses_raise_llm_error(fake_create) -> None:
    fake_create(
        completion({"role": "assistant", "content": "   "}),
        completion({"role": "assistant", "content": None}),
    )

    with pytest.raises(llm_client.LLMError):
        await llm_client.chat([{"role": "user", "content": "hi"}])


async def test_empty_content_with_tool_calls_is_usable(fake_create) -> None:
    models = fake_create(
        completion(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-0",
                        "type": "function",
                        "function": {"name": "get_balances", "arguments": "{}"},
                    }
                ],
            }
        )
    )

    message = await llm_client.chat([{"role": "user", "content": "balance?"}])

    assert message.tool_calls[0].function.name == "get_balances"
    assert len(models) == 1


async def test_client_is_rebuilt_when_the_event_loop_changes() -> None:
    first = llm_client.get_client()

    assert llm_client.get_client() is first
    second = await _client_from_another_loop()
    assert second is not first


async def _client_from_another_loop():
    """Build the client from a separate event loop."""
    import asyncio

    return await asyncio.to_thread(lambda: asyncio.run(_get_client()))


async def _get_client():
    """Return the client for whichever loop is running."""
    return llm_client.get_client()
