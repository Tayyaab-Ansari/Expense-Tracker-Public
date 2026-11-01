"""Tests for the budget service."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.services import budgets
from app.services import transactions as tx
from app.services.categories import resolve_category

DAY = date(2026, 10, 15)
YEAR, MONTH = 2026, 10


def spend(session: Session, user: User, amount: str, category: str = "food", day: date = DAY) -> None:
    """Record one expense."""
    tx.add_transactions(
        session, user.id, [{"amount": amount, "category": category, "date": day.isoformat()}]
    )


def test_set_budget_upserts(session: Session, user: User) -> None:
    first = budgets.set_budget(session, user.id, "food", "15000")
    second = budgets.set_budget(session, user.id, "Food", "20000")

    assert first == {
        "category": "Food",
        "amount": Decimal("15000.00"),
        "amount_display": "Rs 15,000",
    }
    assert second["amount"] == Decimal("20000.00")

    status = budgets.budget_status(session, user.id, YEAR, MONTH)
    assert len(status["items"]) == 1
    assert status["items"][0]["budget"] == Decimal("20000.00")


def test_set_budget_rejects_income_category(session: Session, user: User) -> None:
    with pytest.raises(ValueError, match="income category"):
        budgets.set_budget(session, user.id, "salary", "15000")


def test_set_budget_rejects_unknown_category(session: Session, user: User) -> None:
    with pytest.raises(ValueError, match="Unknown expense category"):
        budgets.set_budget(session, user.id, "spaceship parts", "15000")


def test_set_budget_rejects_bad_amount(session: Session, user: User) -> None:
    for amount in ("0", "-5", "abc"):
        with pytest.raises(ValueError):
            budgets.set_budget(session, user.id, "food", amount)


def test_remove_budget(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")

    assert budgets.remove_budget(session, user.id, "food") == {
        "category": "Food",
        "removed": True,
    }
    assert budgets.budget_status(session, user.id, YEAR, MONTH)["items"] == []

    with pytest.raises(ValueError, match="No budget set"):
        budgets.remove_budget(session, user.id, "food")


def test_status_math_and_order(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "10000")
    budgets.set_budget(session, user.id, "transport", "5000")
    spend(session, user, "2500", "food")
    spend(session, user, "4000", "transport")

    status = budgets.budget_status(session, user.id, YEAR, MONTH)

    assert status["month"] == "2026-10"
    assert [item["category"] for item in status["items"]] == ["Transport", "Food"]

    transport, food = status["items"]
    assert transport["spent"] == Decimal("4000.00")
    assert transport["remaining"] == Decimal("1000.00")
    assert transport["pct"] == Decimal("80.00")
    assert food["pct"] == Decimal("25.00")
    assert status["total_budget"] == Decimal("15000.00")
    assert status["total_spent"] == Decimal("6500.00")


def test_status_ignores_other_months_and_deleted(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "10000")
    spend(session, user, "1000", "food", date(2026, 9, 30))
    saved = tx.add_transactions(
        session, user.id, [{"amount": "500", "category": "food", "date": DAY.isoformat()}]
    )["saved"]
    tx.delete_transaction(session, user.id, saved[0]["id"])

    status = budgets.budget_status(session, user.id, YEAR, MONTH)

    assert status["items"][0]["spent"] == Decimal("0.00")
    assert status["total_spent"] == Decimal("0.00")


def test_status_with_no_budgets(session: Session, user: User) -> None:
    status = budgets.budget_status(session, user.id, YEAR, MONTH)

    assert status["items"] == []
    assert status["total_budget"] == Decimal("0.00")
    assert status["total_spent"] == Decimal("0.00")


def test_warning_at_eighty_percent(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")
    spend(session, user, "12300", "food")
    food = resolve_category(session, user.id, "food", "expense")

    assert (
        budgets.warning_for(session, user.id, food.id, DAY)
        == "⚠️ Food: Rs 12,300 / Rs 15,000 used (82%)"
    )


def test_warning_when_exceeded(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")
    spend(session, user, "15600", "food")
    food = resolve_category(session, user.id, "food", "expense")

    assert (
        budgets.warning_for(session, user.id, food.id, DAY)
        == "🚨 Food budget exceeded: Rs 15,600 / Rs 15,000 (104%)"
    )


def test_warning_at_exactly_the_budget(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")
    spend(session, user, "15000", "food")
    food = resolve_category(session, user.id, "food", "expense")

    assert budgets.warning_for(session, user.id, food.id, DAY).startswith("🚨")


def test_no_warning_below_eighty_percent(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")
    spend(session, user, "11000", "food")
    food = resolve_category(session, user.id, "food", "expense")

    assert budgets.warning_for(session, user.id, food.id, DAY) is None


def test_no_warning_without_a_budget(session: Session, user: User) -> None:
    spend(session, user, "99000", "food")
    food = resolve_category(session, user.id, "food", "expense")

    assert budgets.warning_for(session, user.id, food.id, DAY) is None


def test_warnings_are_returned_by_add_transactions(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")
    budgets.set_budget(session, user.id, "transport", "1000")

    result = tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "12300", "category": "food", "date": DAY.isoformat()},
            {"amount": "1200", "category": "transport", "date": DAY.isoformat()},
            {"amount": "200", "category": "fuel", "date": DAY.isoformat()},
            {"type": "income", "amount": "5000", "category": "salary", "date": DAY.isoformat()},
        ],
    )

    assert result["budget_warnings"] == [
        "⚠️ Food: Rs 12,300 / Rs 15,000 used (82%)",
        "🚨 Transport budget exceeded: Rs 1,200 / Rs 1,000 (120%)",
    ]


def test_no_warnings_key_when_nothing_to_warn_about(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")

    result = tx.add_transactions(
        session, user.id, [{"amount": "100", "category": "food", "date": DAY.isoformat()}]
    )

    assert "budget_warnings" not in result


def test_warnings_are_deduplicated_per_category(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")

    result = tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "6000", "category": "food", "date": DAY.isoformat()},
            {"amount": "6500", "category": "food", "date": DAY.isoformat()},
        ],
    )

    assert result["budget_warnings"] == ["⚠️ Food: Rs 12,500 / Rs 15,000 used (83%)"]


def test_warning_counts_only_that_month(session: Session, user: User) -> None:
    budgets.set_budget(session, user.id, "food", "15000")
    spend(session, user, "14000", "food", date(2026, 9, 10))
    spend(session, user, "1000", "food", DAY)
    food = resolve_category(session, user.id, "food", "expense")

    assert budgets.warning_for(session, user.id, food.id, DAY) is None
    assert budgets.warning_for(session, user.id, food.id, date(2026, 9, 10)).startswith("⚠️")
