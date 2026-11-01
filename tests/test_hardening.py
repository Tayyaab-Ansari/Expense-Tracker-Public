"""Tests for rate limiting, startup checks and the database URL normaliser."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app import handler, main
from app.config import Settings, settings
from app.whatsapp.payload import IncomingMessage

asyncio_test = pytest.mark.asyncio(loop_scope="function")


def message(index: int = 1, sender: str = "923000000000") -> IncomingMessage:
    """One inbound text message."""
    return IncomingMessage(
        message_id=f"wamid.{index}",
        sender=sender,
        type="text",
        text="chai 150",
        contact_name="Test",
    )


@pytest.fixture
def stub_handler(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record outbound texts and answer every message instantly."""
    sent: list[str] = []

    async def send_text(to: str, body: str) -> None:
        sent.append(body)

    async def noop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(handler, "send_text", send_text)
    monkeypatch.setattr(handler, "mark_as_read", noop)
    monkeypatch.setattr(handler, "_answer_text", noop)
    handler._recent.clear()
    handler._warned.clear()
    return sent


@asyncio_test
async def test_messages_under_the_limit_are_answered(stub_handler: list[str]) -> None:
    for index in range(handler.RATE_LIMIT):
        await handler.handle_message(message(index))

    assert stub_handler == []
    assert len(handler._recent["923000000000"]) == handler.RATE_LIMIT


@asyncio_test
async def test_limit_warns_once_then_stays_silent(stub_handler: list[str]) -> None:
    for index in range(handler.RATE_LIMIT + 5):
        await handler.handle_message(message(index))

    assert stub_handler == [handler.RATE_LIMIT_REPLY]


@asyncio_test
async def test_window_resets_after_the_period(
    stub_handler: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(handler, "monotonic", lambda: clock[0])

    for index in range(handler.RATE_LIMIT + 2):
        await handler.handle_message(message(index))
    assert stub_handler == [handler.RATE_LIMIT_REPLY]

    clock[0] += handler.RATE_WINDOW + 1
    await handler.handle_message(message(99))

    assert stub_handler == [handler.RATE_LIMIT_REPLY]
    assert "923000000000" not in handler._warned

    for index in range(handler.RATE_LIMIT + 2):
        await handler.handle_message(message(100 + index))
    assert stub_handler == [handler.RATE_LIMIT_REPLY, handler.RATE_LIMIT_REPLY]


@asyncio_test
async def test_limits_are_per_sender(stub_handler: list[str]) -> None:
    for index in range(handler.RATE_LIMIT + 2):
        await handler.handle_message(message(index, sender="923000000000"))

    await handler.handle_message(message(1, sender="923111111111"))

    assert stub_handler == [handler.RATE_LIMIT_REPLY]


@pytest.fixture
def isolated_startup(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the application lifespan against the test database."""

    @contextmanager
    def test_scope():
        """Session scope backed by the in-memory test database."""
        yield session

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "session_scope", test_scope)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://user:pw@host:5432/db", "postgresql+psycopg://user:pw@host:5432/db"),
        ("postgresql://user:pw@host/db", "postgresql+psycopg://user:pw@host/db"),
        ("postgresql+psycopg://user:pw@host/db", "postgresql+psycopg://user:pw@host/db"),
        ("sqlite:///./data/expense.db", "sqlite:///./data/expense.db"),
        ("postgresql+asyncpg://user:pw@host/db", "postgresql+asyncpg://user:pw@host/db"),
    ],
)
def test_database_url_is_normalised(given: str, expected: str) -> None:
    assert Settings(DATABASE_URL=given).DATABASE_URL == expected


def test_normalised_postgres_url_is_not_sqlite() -> None:
    assert Settings(DATABASE_URL="postgres://user:pw@host/db").is_sqlite is False


def test_missing_reports_empty_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ALLOWED_NUMBERS", "")
    monkeypatch.setattr(settings, "WA_ACCESS_TOKEN", SecretStr(""))
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", SecretStr("sk-test"))

    missing = main._missing(main.REQUIRED_IN_PRODUCTION)

    assert "ALLOWED_NUMBERS" in missing
    assert "WA_ACCESS_TOKEN" in missing
    assert "OPENROUTER_API_KEY" not in missing


def test_production_refuses_to_start_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WA_APP_SECRET", SecretStr(""))

    with pytest.raises(RuntimeError, match="WA_APP_SECRET"):
        with TestClient(main.app):
            pass


def test_development_starts_with_missing_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "WA_APP_SECRET", SecretStr(""))
    monkeypatch.setattr(settings, "ALLOWED_NUMBERS", "")

    with caplog.at_level("WARNING"), TestClient(main.app) as client:
        assert client.get("/").json() == {"status": "up"}

    assert "WA_APP_SECRET" in caplog.text
    assert "ALLOWED_NUMBERS" in caplog.text


def test_health_checks_the_database() -> None:
    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok", "db": "ok"}


def test_health_reports_a_broken_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Session:
        raise RuntimeError("no database")

    client = TestClient(main.app, raise_server_exceptions=False)
    monkeypatch.setattr(main, "session_scope", broken)

    assert client.get("/health").status_code == 503
