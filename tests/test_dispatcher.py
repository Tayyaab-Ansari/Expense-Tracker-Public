"""Tests for the LLM tool dispatcher."""

import json
from datetime import date

from sqlalchemy.orm import Session

from app.llm.dispatcher import REGISTRY, dispatch
from app.llm.tools import TOOL_NAMES
from app.models import User
from app.services import transactions as tx
from app.utils.dates import resolve_period

DAY = date(2026, 10, 15)


def test_registry_covers_every_tool() -> None:
    assert set(REGISTRY) == TOOL_NAMES


def test_add_transactions_via_dispatch(session: Session, user: User) -> None:
    result = dispatch(
        session,
        user.id,
        "add_transactions",
        {"items": [{"amount": 150, "category": "food", "date": DAY.isoformat()}]},
        {"source": "voice", "raw_text": "chai 150"},
    )

    assert "error" not in result
    assert result["saved"][0]["amount"] == "150.00"
    assert result["saved"][0]["date"] == DAY.isoformat()
    assert result["saved"][0]["category"] == "Food"
    assert json.dumps(result)

    stored = tx.get_transaction(session, user.id, result["saved"][0]["id"])
    assert stored.source == "voice"
    assert stored.raw_text == "chai 150"


def test_unknown_tool_returns_error(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "launch_rocket", {})

    assert result == {"error": "Unknown tool: launch_rocket"}


def test_invalid_account_error_lists_valid_accounts(session: Session, user: User) -> None:
    result = dispatch(
        session,
        user.id,
        "add_transactions",
        {"items": [{"amount": 150, "category": "food", "account": "paypal"}]},
    )

    assert "paypal" in result["error"]
    for name in ("Cash", "JazzCash", "Easypaisa", "Bank"):
        assert name in result["error"]


def test_summary_with_period_this_month(session: Session, user: User) -> None:
    start, end = resolve_period("this_month")
    dispatch(
        session,
        user.id,
        "add_transactions",
        {
            "items": [
                {"amount": 150, "category": "food", "date": start.isoformat()},
                {"amount": 50, "category": "transport", "date": end.isoformat()},
            ]
        },
    )

    result = dispatch(session, user.id, "spending_summary", {"period": "this_month"})

    assert result["start"] == start.isoformat()
    assert result["end"] == end.isoformat()
    assert result["total"] == "200.00"
    assert result["total_display"] == "Rs 200"
    assert result["count"] == 2
    assert {row["category"] for row in result["by_category"]} == {"Food", "Transport"}
    assert result["by_category"][0]["pct"] == "75.00"
    assert json.dumps(result)


def test_summary_defaults_to_this_month(session: Session, user: User) -> None:
    start, _ = resolve_period("this_month")

    result = dispatch(session, user.id, "spending_summary", {})

    assert result["start"] == start.isoformat()
    assert result["total"] == "0.00"


def test_unknown_period_returns_error(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "spending_summary", {"period": "next_century"})

    assert "next_century" in result["error"]


def test_missing_required_argument_returns_error(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "delete_transaction", {})

    assert result["error"].startswith("Missing argument")


def test_invalid_amount_returns_error(session: Session, user: User) -> None:
    result = dispatch(
        session, user.id, "add_transactions", {"items": [{"amount": -5, "category": "food"}]}
    )

    assert "error" in result
    assert "greater than zero" in result["error"]


def test_undo_last_without_transactions_returns_error(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "undo_last", {})

    assert "error" in result


def test_balances_are_serialised(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "get_balances", {})

    assert [row["name"] for row in result["accounts"]][0] == "Cash"
    assert result["accounts"][0]["balance"] == "0.00"
    assert json.dumps(result)


def test_set_account_balance_adds_amount_display(session: Session, user: User) -> None:
    result = dispatch(
        session, user.id, "set_account_balance", {"account": "jazz", "balance": 2500}
    )

    assert result["name"] == "JazzCash"
    assert result["balance"] == "2500.00"
    assert result["balance_display"] == "Rs 2,500"


def test_search_without_period_is_not_date_bounded(session: Session, user: User) -> None:
    dispatch(
        session,
        user.id,
        "add_transactions",
        {"items": [{"amount": 700, "category": "food", "date": "2024-03-04"}]},
    )

    result = dispatch(session, user.id, "search_transactions", {"text": None})

    assert result["count_total"] == 1
    assert result["items"][0]["date"] == "2024-03-04"


def test_search_sorted_and_limited(session: Session, user: User) -> None:
    dispatch(
        session,
        user.id,
        "add_transactions",
        {
            "items": [
                {"amount": 100, "category": "food", "date": DAY.isoformat()},
                {"amount": 900, "category": "food", "date": DAY.isoformat()},
            ]
        },
    )

    result = dispatch(
        session, user.id, "search_transactions", {"sort": "largest", "limit": 1}
    )

    assert [item["amount"] for item in result["items"]] == ["900.00"]
    assert result["count_total"] == 2
    assert result["total_amount"] == "1000.00"


def test_update_and_delete_via_dispatch(session: Session, user: User) -> None:
    dispatch(
        session,
        user.id,
        "add_transactions",
        {"items": [{"amount": 150, "category": "food", "date": DAY.isoformat()}]},
    )

    updated = dispatch(
        session, user.id, "update_transaction", {"ref": "last", "amount": 120, "account": "bank"}
    )
    assert updated["after"]["amount"] == "120.00"
    assert updated["after"]["account"] == "Bank"

    deleted = dispatch(session, user.id, "delete_transaction", {"ref": "last"})
    assert deleted["deleted"]["amount"] == "120.00"
    assert tx.recent(session, user.id) == []


def test_add_category_via_dispatch(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "add_category", {"name": "side hustle", "kind": "income"})

    assert result["name"] == "Side Hustle"
    assert result["kind"] == "income"


def test_set_default_account_via_dispatch(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "set_default_account", {"account": "atm"})

    assert result["name"] == "Bank"
    assert result["is_default"] is True
