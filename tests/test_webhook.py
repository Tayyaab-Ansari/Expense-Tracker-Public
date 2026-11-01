"""Tests for the WhatsApp webhook endpoints."""

import hashlib
import hmac
import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import handler
from app.config import settings
from app.main import app
from app.models import ProcessedMessage
from app.whatsapp import webhook
from app.whatsapp.payload import IncomingMessage

SECRET = "testsecret"
VERIFY_TOKEN = "testverify"
OWNER = "923000000000"
STRANGER = "923999999999"


def sign(raw: bytes) -> str:
    """Build the X-Hub-Signature-256 header for a raw body."""
    return "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def text_payload(message_id: str, sender: str = OWNER) -> bytes:
    """Raw JSON body for one inbound text message."""
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "111"},
                                "contacts": [{"profile": {"name": "Test"}, "wa_id": sender}],
                                "messages": [
                                    {
                                        "from": sender,
                                        "id": message_id,
                                        "timestamp": "1761900000",
                                        "type": "text",
                                        "text": {"body": "chai 150"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


STATUS_PAYLOAD = json.dumps(
    {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "111"},
                            "statuses": [
                                {
                                    "id": "wamid.sent",
                                    "status": "delivered",
                                    "timestamp": "1761900001",
                                    "recipient_id": OWNER,
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
).encode()


@pytest.fixture
def handled(session: Session, monkeypatch: pytest.MonkeyPatch) -> list[IncomingMessage]:
    """Webhook wired to the test database and a recording handler."""
    monkeypatch.setattr(settings, "WA_APP_SECRET", SecretStr(SECRET))
    monkeypatch.setattr(settings, "WA_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setattr(settings, "ALLOWED_NUMBERS", OWNER)

    @contextmanager
    def test_scope() -> Iterator[Session]:
        """Session scope backed by the in-memory test database."""
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(webhook, "session_scope", test_scope)

    recorded: list[IncomingMessage] = []

    async def recorder(msg: IncomingMessage) -> None:
        """Record a message instead of replying on WhatsApp."""
        recorded.append(msg)

    monkeypatch.setattr(handler, "handle_message", recorder)
    monkeypatch.setattr(webhook, "handle_message", recorder)
    return recorded


@pytest.fixture
def client() -> TestClient:
    """Test client that does not run the application lifespan."""
    return TestClient(app)


def post(client: TestClient, raw: bytes, signature: str | None = None):
    """POST a raw webhook body with a signature header."""
    headers = {"content-type": "application/json"}
    headers["x-hub-signature-256"] = sign(raw) if signature is None else signature
    return client.post("/webhook", content=raw, headers=headers)


def test_verification_succeeds(client: TestClient, handled: list) -> None:
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 200
    assert response.text == "1158201444"


def test_verification_wrong_token_is_forbidden(client: TestClient, handled: list) -> None:
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 403


def test_verification_wrong_mode_is_forbidden(client: TestClient, handled: list) -> None:
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 403


def test_bad_signature_is_forbidden(client: TestClient, handled: list) -> None:
    raw = text_payload("wamid.1")

    response = post(client, raw, signature="sha256=" + "0" * 64)

    assert response.status_code == 403
    assert handled == []


def test_missing_signature_is_forbidden(client: TestClient, handled: list) -> None:
    response = client.post(
        "/webhook", content=text_payload("wamid.1"), headers={"content-type": "application/json"}
    )

    assert response.status_code == 403
    assert handled == []


def test_tampered_body_is_forbidden(client: TestClient, handled: list) -> None:
    raw = text_payload("wamid.1")

    response = post(client, raw + b" ", signature=sign(raw))

    assert response.status_code == 403
    assert handled == []


def test_valid_text_from_allowed_number_is_handled(client: TestClient, handled: list) -> None:
    response = post(client, text_payload("wamid.1"))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(handled) == 1
    assert handled[0].sender == OWNER
    assert handled[0].text == "chai 150"
    assert handled[0].type == "text"


def test_duplicate_message_is_handled_once(
    client: TestClient, handled: list, session: Session
) -> None:
    raw = text_payload("wamid.dup")

    first = post(client, raw)
    second = post(client, raw)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {"ok": True}
    assert len(handled) == 1
    recorded = session.scalars(
        select(ProcessedMessage).where(ProcessedMessage.wa_message_id == "wamid.dup")
    ).all()
    assert len(recorded) == 1


def test_stranger_is_ignored(client: TestClient, handled: list) -> None:
    response = post(client, text_payload("wamid.2", sender=STRANGER))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert handled == []


def test_status_only_payload_is_ignored(client: TestClient, handled: list) -> None:
    response = post(client, STATUS_PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert handled == []


def test_invalid_json_is_accepted_without_handling(client: TestClient, handled: list) -> None:
    response = post(client, b"not json")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert handled == []


def test_health_and_privacy_routes(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").json() == {"status": "up"}

    privacy = client.get("/privacy")
    assert privacy.status_code == 200
    assert "text/html" in privacy.headers["content-type"]
