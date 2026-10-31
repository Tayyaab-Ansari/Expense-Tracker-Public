"""Tests for the analytics service."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.services import analytics as an
from app.services import transactions as tx

START = date(2026, 10, 1)
END = date(2026, 10, 31)
DAY = date(2026, 10, 15)
EARLIER = date(2026, 10, 14)
OUTSIDE = date(2026, 9, 20)


@pytest.fixture
def seeded(session: Session, user: User) -> User:
    """User with a known spread of transactions inside and outside the period."""
    tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "description": "Chai at Dhaba", "date": DAY.isoformat()},
            {"amount": "850", "category": "food", "description": "dinner", "date": DAY.isoformat()},
            {"amount": "200", "category": "transport", "account": "jazz", "date": EARLIER.isoformat()},
            {"amount": "1000", "category": "groceries", "account": "bank", "date": DAY.isoformat()},
            {"type": "income", "amount": "50000", "category": "salary", "account": "bank", "date": DAY.isoformat()},
            {"amount": "700", "category": "food", "date": OUTSIDE.isoformat()},
        ],
    )
    return user


def test_summary_totals_and_pct(session: Session, seeded: User) -> None:
    summary = an.spending_summary(session, seeded.id, START, END)

    assert summary["start"] == START
    assert summary["end"] == END
    assert summary["type"] == "expense"
    assert summary["total"] == Decimal("2200.00")
    assert summary["count"] == 4

    by_category = {row["category"]: row for row in summary["by_category"]}
    assert by_category["Food"]["total"] == Decimal("1000.00")
    assert by_category["Food"]["count"] == 2
    assert by_category["Food"]["pct"] == Decimal("45.45")
    assert by_category["Transport"]["pct"] == Decimal("9.09")

    totals = [row["total"] for row in summary["by_category"]]
    assert totals == sorted(totals, reverse=True)
    assert {row["account"] for row in summary["by_account"]} == {"Cash", "Bank", "JazzCash"}


def test_summary_income_and_filters(session: Session, seeded: User) -> None:
    assert an.spending_summary(session, seeded.id, START, END, type="income")["total"] == Decimal(
        "50000.00"
    )
    assert an.spending_summary(session, seeded.id, START, END, category="food")[
        "total"
    ] == Decimal("1000.00")
    assert an.spending_summary(session, seeded.id, START, END, account="bank")[
        "total"
    ] == Decimal("1000.00")


def test_summary_empty_period(session: Session, seeded: User) -> None:
    summary = an.spending_summary(session, seeded.id, date(2020, 1, 1), date(2020, 1, 31))

    assert summary["total"] == Decimal("0.00")
    assert summary["count"] == 0
    assert summary["by_category"] == []
    assert summary["by_account"] == []


def test_search_min_amount(session: Session, seeded: User) -> None:
    result = an.search_transactions(session, seeded.id, min_amount="500", type="expense")

    assert result["count_total"] == 3
    assert all(item["amount"] >= Decimal("500") for item in result["items"])
    assert result["total_amount"] == Decimal("2550.00")


def test_search_text_is_case_insensitive(session: Session, seeded: User) -> None:
    result = an.search_transactions(session, seeded.id, text="CHAI")

    assert result["count_total"] == 1
    assert result["items"][0]["description"] == "Chai at Dhaba"


def test_search_by_category_and_period(session: Session, seeded: User) -> None:
    result = an.search_transactions(session, seeded.id, category="food", start=START, end=END)

    assert result["count_total"] == 2
    assert result["total_amount"] == Decimal("1000.00")
    assert {item["category"] for item in result["items"]} == {"Food"}


def test_search_sort_largest(session: Session, seeded: User) -> None:
    result = an.search_transactions(
        session, seeded.id, type="expense", start=START, end=END, sort="largest"
    )

    amounts = [item["amount"] for item in result["items"]]
    assert amounts == sorted(amounts, reverse=True)
    assert amounts[0] == Decimal("1000.00")


def test_search_limit_is_capped(session: Session, seeded: User) -> None:
    assert len(an.search_transactions(session, seeded.id, limit=99)["items"]) <= an.MAX_SEARCH_LIMIT


def test_search_rejects_unknown_sort(session: Session, seeded: User) -> None:
    with pytest.raises(ValueError):
        an.search_transactions(session, seeded.id, sort="cheapest")


def test_deleted_transactions_are_excluded(session: Session, seeded: User) -> None:
    before = an.spending_summary(session, seeded.id, START, END)
    target = an.search_transactions(session, seeded.id, text="dinner")["items"][0]

    tx.delete_transaction(session, seeded.id, target["id"])
    after = an.spending_summary(session, seeded.id, START, END)

    assert before["total"] - after["total"] == Decimal("850.00")
    assert after["count"] == before["count"] - 1
    assert an.search_transactions(session, seeded.id, text="dinner")["count_total"] == 0
    assert Decimal("850.00") not in an.daily_totals(session, seeded.id, START, END).values()


def test_daily_totals_only_days_with_data(session: Session, seeded: User) -> None:
    totals = an.daily_totals(session, seeded.id, START, END)

    assert totals == {EARLIER: Decimal("200.00"), DAY: Decimal("2000.00")}
    assert list(totals) == [EARLIER, DAY]
    assert an.daily_totals(session, seeded.id, START, END, type="income") == {
        DAY: Decimal("50000.00")
    }


def test_top_expenses(session: Session, seeded: User) -> None:
    top = an.top_expenses(session, seeded.id, START, END, n=2)

    assert [item["amount"] for item in top] == [Decimal("1000.00"), Decimal("850.00")]
    assert all(item["type"] == "expense" for item in top)
