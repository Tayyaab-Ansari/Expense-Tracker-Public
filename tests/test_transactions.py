"""Tests for the transaction service."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.services import transactions as tx
from app.utils.dates import human_date

DAY = date(2026, 10, 15)
EARLIER = date(2026, 10, 14)


def test_add_multiple_transactions(session: Session, user: User) -> None:
    result = tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "description": "chai", "date": DAY.isoformat()},
            {"amount": "80", "category": "transport", "date": DAY.isoformat()},
        ],
    )

    assert [entry["amount"] for entry in result["saved"]] == [
        Decimal("150.00"),
        Decimal("80.00"),
    ]
    assert [entry["category"] for entry in result["saved"]] == ["Food", "Transport"]
    assert len(result["confirmation_text"].splitlines()) == 2


def test_add_uses_default_cash_account(session: Session, user: User) -> None:
    saved = tx.add_transactions(
        session, user.id, [{"amount": "150", "category": "food", "date": DAY.isoformat()}]
    )["saved"]

    assert saved[0]["account"] == "Cash"


def test_unknown_category_falls_back_to_other(session: Session, user: User) -> None:
    saved = tx.add_transactions(
        session,
        user.id,
        [{"amount": "150", "category": "spaceship parts", "date": DAY.isoformat()}],
    )["saved"]

    assert saved[0]["category"] == "Other"


def test_income_uses_money_emoji(session: Session, user: User) -> None:
    result = tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "date": DAY.isoformat()},
            {
                "type": "income",
                "amount": "50,000",
                "category": "salary",
                "date": DAY.isoformat(),
            },
        ],
    )

    expense_line, income_line = result["confirmation_text"].splitlines()
    assert expense_line.startswith("✅")
    assert income_line.startswith("💰")
    assert "Rs 50,000" in income_line


def test_confirmation_line_format(session: Session, user: User) -> None:
    result = tx.add_transactions(
        session,
        user.id,
        [{"amount": "150", "category": "food", "description": "chai", "date": DAY.isoformat()}],
    )
    tx_id = result["saved"][0]["id"]

    assert result["confirmation_text"] == f"✅ #{tx_id} Food · Rs 150 · Cash · {human_date(DAY)} — chai"


def test_update_last_transaction(session: Session, user: User) -> None:
    tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "date": EARLIER.isoformat()},
            {"amount": "80", "category": "transport", "date": DAY.isoformat()},
        ],
    )

    result = tx.update_transaction(
        session, user.id, "last", amount="95", category="fuel", account="jazz"
    )

    assert result["before"]["amount"] == Decimal("80.00")
    assert result["after"]["amount"] == Decimal("95.00")
    assert result["after"]["category"] == "Fuel"
    assert result["after"]["account"] == "JazzCash"
    assert result["confirmation_text"].startswith(f"✏️ #{result['after']['id']} updated: Fuel")
    assert tx.get_transaction(session, user.id, result["after"]["id"]).updated_at is not None


def test_update_type_reresolves_category(session: Session, user: User) -> None:
    tx.add_transactions(
        session, user.id, [{"amount": "150", "category": "food", "date": DAY.isoformat()}]
    )

    result = tx.update_transaction(session, user.id, "last", type="income")

    assert result["after"]["type"] == "income"
    assert result["after"]["category"] == "Other Income"


def test_delete_transaction_is_soft(session: Session, user: User) -> None:
    saved = tx.add_transactions(
        session, user.id, [{"amount": "150", "category": "food", "date": DAY.isoformat()}]
    )["saved"]
    tx_id = saved[0]["id"]

    result = tx.delete_transaction(session, user.id, tx_id)

    assert result["deleted"]["amount"] == Decimal("150.00")
    assert result["confirmation_text"] == f"🗑️ Deleted #{tx_id} Food · Rs 150"
    assert tx.get_transaction(session, user.id, tx_id) is None
    assert tx.recent(session, user.id) == []


def test_undo_last_twice(session: Session, user: User) -> None:
    tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "date": EARLIER.isoformat()},
            {"amount": "80", "category": "transport", "date": DAY.isoformat()},
        ],
    )

    first = tx.undo_last(session, user.id)
    second = tx.undo_last(session, user.id)

    assert first["deleted"]["amount"] == Decimal("80.00")
    assert first["confirmation_text"].startswith("↩️ Undone")
    assert second["deleted"]["amount"] == Decimal("150.00")
    assert tx.recent(session, user.id) == []
    with pytest.raises(ValueError):
        tx.undo_last(session, user.id)


def test_recent_is_newest_first(session: Session, user: User) -> None:
    tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "date": EARLIER.isoformat()},
            {"amount": "80", "category": "transport", "date": DAY.isoformat()},
        ],
    )

    assert [entry["date"] for entry in tx.recent(session, user.id)] == [DAY, EARLIER]
    assert len(tx.recent(session, user.id, limit=1)) == 1


@pytest.mark.parametrize("amount", ["0", "-5", "abc", ""])
def test_invalid_amount_rejected(session: Session, user: User, amount: str) -> None:
    with pytest.raises(ValueError):
        tx.add_transactions(
            session, user.id, [{"amount": amount, "category": "food", "date": DAY.isoformat()}]
        )


def test_invalid_type_and_empty_items_rejected(session: Session, user: User) -> None:
    with pytest.raises(ValueError):
        tx.add_transactions(session, user.id, [])
    with pytest.raises(ValueError):
        tx.add_transactions(
            session, user.id, [{"type": "refund", "amount": "5", "date": DAY.isoformat()}]
        )


def test_find_rejects_unknown_reference(session: Session, user: User) -> None:
    with pytest.raises(ValueError):
        tx._find(session, user.id, 999)
    with pytest.raises(ValueError):
        tx._find(session, user.id, "nonsense")
