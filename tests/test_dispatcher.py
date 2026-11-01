"""Tests for the LLM tool dispatcher."""

import json
from datetime import date

from sqlalchemy.orm import Session

from app.llm.dispatcher import REGISTRY, dispatch
from app.llm.tools import TOOL_NAMES
from app.models import User
from app.services import transactions as tx
from app.utils.dates import parse_month, resolve_period

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


def test_budget_tools_via_dispatch(session: Session, user: User) -> None:
    assert dispatch(session, user.id, "set_budget", {"category": "food", "amount": 15000}) == {
        "category": "Food",
        "amount": "15000.00",
        "amount_display": "Rs 15,000",
    }

    dispatch(
        session,
        user.id,
        "add_transactions",
        {"items": [{"amount": 12300, "category": "food", "date": "2026-10-05"}]},
    )
    status = dispatch(session, user.id, "budget_status", {"month": "2026-10"})

    assert status["month"] == "2026-10"
    assert status["items"][0] == {
        "category": "Food",
        "budget": "15000.00",
        "spent": "12300.00",
        "remaining": "2700.00",
        "pct": "82.00",
    }
    assert json.dumps(status)

    assert dispatch(session, user.id, "remove_budget", {"category": "food"}) == {
        "category": "Food",
        "removed": True,
    }
    assert dispatch(session, user.id, "remove_budget", {"category": "food"})["error"]


def test_set_budget_rejects_income_category_via_dispatch(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "set_budget", {"category": "salary", "amount": 100})

    assert "income category" in result["error"]


def test_budget_status_defaults_to_this_month(session: Session, user: User) -> None:
    year, month = parse_month(None)

    result = dispatch(session, user.id, "budget_status", {})

    assert result["month"] == f"{year:04d}-{month:02d}"


def test_compare_months_via_dispatch(session: Session, user: User) -> None:
    dispatch(
        session,
        user.id,
        "add_transactions",
        {
            "items": [
                {"amount": 1000, "category": "food", "date": "2026-09-05"},
                {"amount": 3000, "category": "food", "date": "2026-10-05"},
            ]
        },
    )

    result = dispatch(
        session, user.id, "compare_months", {"month_a": "2026-09", "month_b": "2026-10"}
    )

    assert result["label_a"] == "2026-09"
    assert result["label_b"] == "2026-10"
    assert result["a"]["expense"] == "1000.00"
    assert result["b"]["expense"] == "3000.00"
    assert result["expense_change_pct"] == "200.00"
    assert result["categories"][0]["category"] == "Food"
    assert json.dumps(result)


def test_compare_months_defaults_to_last_and_this_month(session: Session, user: User) -> None:
    this_year, this_month = parse_month("this_month")
    last_year, last_month = parse_month("last_month")

    result = dispatch(session, user.id, "compare_months", {})

    assert result["label_a"] == f"{last_year:04d}-{last_month:02d}"
    assert result["label_b"] == f"{this_year:04d}-{this_month:02d}"


def test_get_insights_via_dispatch(session: Session, user: User) -> None:
    dispatch(
        session,
        user.id,
        "add_transactions",
        {"items": [{"amount": 500, "category": "food", "date": "2026-10-05"}]},
    )
    dispatch(session, user.id, "set_budget", {"category": "food", "amount": 600})

    result = dispatch(session, user.id, "get_insights", {"month": "2026-10"})

    assert result["period_label"] == "Oct 2026"
    assert result["spent_so_far"] == "500.00"
    assert [item["category"] for item in result["budgets_at_risk"]] == ["Food"]
    assert result["biggest_expense"]["amount"] == "500.00"
    assert json.dumps(result)


def test_invalid_month_returns_error(session: Session, user: User) -> None:
    for name, args in (
        ("budget_status", {"month": "nope"}),
        ("get_insights", {"month": "13"}),
        ("compare_months", {"month_a": "octobre"}),
    ):
        assert "error" in dispatch(session, user.id, name, args)


def test_debt_tools_via_dispatch(session: Session, user: User) -> None:
    lent = dispatch(
        session,
        user.id,
        "record_debt",
        {
            "person": "ali",
            "kind": "lent",
            "amount": 2000,
            "account": "jazz",
            "note": "rent",
            "date": "2026-10-15",
        },
    )

    assert lent["confirmation_text"] == "🤝 Lent Rs 2,000 to Ali · JazzCash · 15 Oct"
    assert lent["person_balance"] == "2000.00"
    assert lent["saved"]["note"] == "rent"
    assert json.dumps(lent)

    repaid = dispatch(
        session, user.id, "record_debt", {"person": "ali", "kind": "repaid_to_me", "amount": 500}
    )
    assert repaid["confirmation_text"] == "✅ Ali paid back Rs 500 · Ali still owes you Rs 1,500"

    summary = dispatch(session, user.id, "debt_summary", {})
    assert summary["people"] == [{"person": "Ali", "net": "1500.00", "status": "owes you"}]
    assert summary["total_owed_to_you"] == "1500.00"
    assert dispatch(session, user.id, "debt_summary", {"person": "ALI"})["people"] == summary["people"]


def test_debt_tool_errors(session: Session, user: User) -> None:
    assert "Unknown debt kind" in dispatch(
        session, user.id, "record_debt", {"person": "ali", "kind": "gifted", "amount": 1}
    )["error"]
    assert dispatch(session, user.id, "record_debt", {"kind": "lent", "amount": 1})[
        "error"
    ].startswith("Missing argument")


def test_goal_tools_via_dispatch(session: Session, user: User) -> None:
    created = dispatch(
        session,
        user.id,
        "create_goal",
        {"name": "Laptop", "target_amount": 150000, "deadline": "2027-06-30"},
    )

    assert created["target"] == "150000.00"
    assert created["deadline"] == "2027-06-30"
    assert created["status"] == "active"

    added = dispatch(session, user.id, "add_to_goal", {"name": "laptop", "amount": 45000})
    assert added["confirmation_text"] == "🎯 Laptop: Rs 45,000 / Rs 150,000 (30%)"

    status = dispatch(session, user.id, "goals_status", {})
    assert status["goals"][0]["saved"] == "45000.00"
    assert status["goals"][0]["pct"] == "30.00"
    assert json.dumps(status)

    assert "No savings goal matching" in dispatch(
        session, user.id, "add_to_goal", {"name": "car", "amount": 1}
    )["error"]


def test_add_to_goal_withdrawal_via_dispatch(session: Session, user: User) -> None:
    dispatch(session, user.id, "create_goal", {"name": "Phone", "target_amount": 50000})
    dispatch(session, user.id, "add_to_goal", {"name": "phone", "amount": 20000})

    result = dispatch(session, user.id, "add_to_goal", {"name": "phone", "amount": -5000})

    assert result["amount"] == "-5000.00"
    assert result["saved"] == "15000.00"


def test_set_default_account_via_dispatch(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "set_default_account", {"account": "atm"})

    assert result["name"] == "Bank"
    assert result["is_default"] is True
