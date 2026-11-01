"""Tests for the debt service."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.services import accounts, debts

DAY = date(2026, 10, 15)


def record(session: Session, user: User, kind: str, amount: str, person: str = "ali", **kwargs):
    """Record one debt entry on a fixed date."""
    kwargs.setdefault("date", DAY.isoformat())
    return debts.record_debt(session, user.id, person, kind, amount, **kwargs)


def test_lent_confirmation_and_normalised_person(session: Session, user: User) -> None:
    result = record(session, user, "lent", "2000", person="  ali  ")

    assert result["saved"]["person"] == "Ali"
    assert result["saved"]["account"] == "Cash"
    assert result["saved"]["amount"] == Decimal("2000.00")
    assert result["confirmation_text"] == "🤝 Lent Rs 2,000 to Ali · Cash · 15 Oct"
    assert result["person_balance"] == Decimal("2000.00")


def test_borrowed_confirmation(session: Session, user: User) -> None:
    result = record(session, user, "borrowed", "5000", account="jazz")

    assert result["confirmation_text"] == "🤝 Borrowed Rs 5,000 from Ali · JazzCash · 15 Oct"
    assert result["person_balance"] == Decimal("-5000.00")


def test_repaid_to_me_reports_remaining_balance(session: Session, user: User) -> None:
    record(session, user, "lent", "2000")

    result = record(session, user, "repaid_to_me", "500")

    assert (
        result["confirmation_text"] == "✅ Ali paid back Rs 500 · Ali still owes you Rs 1,500"
    )
    assert result["person_balance"] == Decimal("1500.00")


def test_repaid_by_me_reports_remaining_balance(session: Session, user: User) -> None:
    record(session, user, "borrowed", "2000")

    result = record(session, user, "repaid_by_me", "500")

    assert result["confirmation_text"] == "✅ Paid back Rs 500 to Ali · You still owe Ali Rs 1,500"
    assert result["person_balance"] == Decimal("-1500.00")


def test_full_repayment_reads_as_settled(session: Session, user: User) -> None:
    record(session, user, "lent", "2000")

    result = record(session, user, "repaid_to_me", "2000")

    assert result["confirmation_text"] == "✅ Ali paid back Rs 2,000 · Settled with Ali"
    assert result["person_balance"] == Decimal("0.00")


def test_overpayment_flips_the_direction(session: Session, user: User) -> None:
    record(session, user, "lent", "1000")

    result = record(session, user, "repaid_to_me", "1500")

    assert "You still owe Ali Rs 500" in result["confirmation_text"]
    assert result["person_balance"] == Decimal("-500.00")


@pytest.mark.parametrize("amount", ["0", "-5", "abc"])
def test_invalid_amount_is_rejected(session: Session, user: User, amount: str) -> None:
    with pytest.raises(ValueError):
        record(session, user, "lent", amount)


def test_invalid_kind_and_person_are_rejected(session: Session, user: User) -> None:
    with pytest.raises(ValueError, match="Unknown debt kind"):
        record(session, user, "gifted", "100")
    with pytest.raises(ValueError, match="must not be empty"):
        record(session, user, "lent", "100", person="   ")


def test_unknown_account_is_rejected(session: Session, user: User) -> None:
    with pytest.raises(ValueError, match="Valid accounts"):
        record(session, user, "lent", "100", account="paypal")


def test_debt_summary_nets_each_person(session: Session, user: User) -> None:
    record(session, user, "lent", "2000", person="ali")
    record(session, user, "repaid_to_me", "500", person="ali")
    record(session, user, "borrowed", "3000", person="sara")
    record(session, user, "repaid_by_me", "1000", person="sara")
    record(session, user, "lent", "700", person="bilal")
    record(session, user, "repaid_to_me", "700", person="bilal")

    summary = debts.debt_summary(session, user.id)

    assert summary["people"] == [
        {"person": "Ali", "net": Decimal("1500.00"), "status": "owes you"},
        {"person": "Bilal", "net": Decimal("0.00"), "status": "settled"},
        {"person": "Sara", "net": Decimal("-2000.00"), "status": "you owe"},
    ]
    assert summary["total_owed_to_you"] == Decimal("1500.00")
    assert summary["total_you_owe"] == Decimal("2000.00")


def test_debt_summary_for_one_person(session: Session, user: User) -> None:
    record(session, user, "lent", "2000", person="ali")
    record(session, user, "borrowed", "3000", person="sara")

    summary = debts.debt_summary(session, user.id, person="ALI")

    assert [row["person"] for row in summary["people"]] == ["Ali"]
    assert summary["total_you_owe"] == Decimal("0.00")


def test_debt_summary_with_no_entries(session: Session, user: User) -> None:
    summary = debts.debt_summary(session, user.id)

    assert summary == {
        "people": [],
        "total_owed_to_you": Decimal("0.00"),
        "total_you_owe": Decimal("0.00"),
    }


def test_debt_history_is_oldest_first(session: Session, user: User) -> None:
    record(session, user, "lent", "2000", note="for rent", date="2026-10-10")
    record(session, user, "repaid_to_me", "500", date="2026-10-15")
    record(session, user, "lent", "100", person="sara", date="2026-10-12")

    history = debts.debt_history(session, user.id, "ali")

    assert [(row["kind"], row["amount"]) for row in history] == [
        ("lent", Decimal("2000.00")),
        ("repaid_to_me", Decimal("500.00")),
    ]
    assert history[0]["note"] == "for rent"
    assert history[0]["account"] == "Cash"
    assert history[0]["date"] == date(2026, 10, 10)


def test_balances_apply_debt_entries_with_an_account(session: Session, user: User) -> None:
    cash = accounts.resolve_account(session, user.id, "cash")
    cash.opening_balance = Decimal("10000.00")
    session.flush()

    record(session, user, "lent", "2000")
    record(session, user, "repaid_to_me", "500")
    record(session, user, "borrowed", "3000", person="sara")
    record(session, user, "repaid_by_me", "1000", person="sara")

    balance = next(
        row["balance"] for row in accounts.get_balances(session, user.id) if row["name"] == "Cash"
    )

    # 10000 - 2000 lent + 500 back + 3000 borrowed - 1000 repaid
    assert balance == Decimal("10500.00")
