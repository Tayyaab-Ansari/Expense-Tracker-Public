"""Tests for the debt and savings goal services."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.services import accounts, debts, goals
from app.utils.dates import today

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


def test_create_goal(session: Session, user: User) -> None:
    result = goals.create_goal(session, user.id, "  Laptop  ", "150000", deadline="2027-06-30")

    assert result["name"] == "Laptop"
    assert result["target"] == Decimal("150000.00")
    assert result["target_display"] == "Rs 150,000"
    assert result["deadline"] == date(2027, 6, 30)
    assert result["status"] == "active"


def test_create_goal_rejects_duplicates_and_bad_input(session: Session, user: User) -> None:
    goals.create_goal(session, user.id, "Laptop", "150000")

    with pytest.raises(ValueError, match="already exists"):
        goals.create_goal(session, user.id, "laptop", "100000")
    with pytest.raises(ValueError, match="must not be empty"):
        goals.create_goal(session, user.id, "   ", "100000")
    for amount in ("0", "-5", "abc"):
        with pytest.raises(ValueError):
            goals.create_goal(session, user.id, f"Goal {amount}", amount)


def test_add_to_goal_confirmation(session: Session, user: User) -> None:
    goals.create_goal(session, user.id, "Laptop", "150000")

    result = goals.add_to_goal(session, user.id, "laptop", "45000", date=DAY.isoformat())

    assert result["confirmation_text"] == "🎯 Laptop: Rs 45,000 / Rs 150,000 (30%)"
    assert result["saved"] == Decimal("45000.00")
    assert result["pct"] == Decimal("30.00")
    assert result["status"] == "active"


def test_add_to_goal_matches_name_fuzzily(session: Session, user: User) -> None:
    goals.create_goal(session, user.id, "New Laptop", "150000")

    assert goals.add_to_goal(session, user.id, "NEW", "1000")["name"] == "New Laptop"
    assert goals.add_to_goal(session, user.id, "laptop", "1000")["name"] == "New Laptop"

    with pytest.raises(ValueError, match="No savings goal matching"):
        goals.add_to_goal(session, user.id, "car", "1000")


def test_contributions_accumulate(session: Session, user: User) -> None:
    goals.create_goal(session, user.id, "Laptop", "150000")
    goals.add_to_goal(session, user.id, "laptop", "45000")

    result = goals.add_to_goal(session, user.id, "laptop", "30000")

    assert result["saved"] == Decimal("75000.00")
    assert result["pct"] == Decimal("50.00")


def test_negative_amount_is_a_withdrawal(session: Session, user: User) -> None:
    goals.create_goal(session, user.id, "Laptop", "150000")
    goals.add_to_goal(session, user.id, "laptop", "45000")

    result = goals.add_to_goal(session, user.id, "laptop", "-15000")

    assert result["amount"] == Decimal("-15000.00")
    assert result["saved"] == Decimal("30000.00")
    assert result["confirmation_text"] == "🎯 Laptop: Rs 30,000 / Rs 150,000 (20%)"

    with pytest.raises(ValueError, match="must not be zero"):
        goals.add_to_goal(session, user.id, "laptop", "0")


def test_goal_is_marked_achieved_and_can_reopen(session: Session, user: User) -> None:
    goals.create_goal(session, user.id, "Phone", "50000")

    reached = goals.add_to_goal(session, user.id, "phone", "50000")
    assert reached["status"] == "achieved"
    assert reached["confirmation_text"] == "🎯 Phone: Rs 50,000 / Rs 50,000 (100%)"

    withdrawn = goals.add_to_goal(session, user.id, "phone", "-1000")
    assert withdrawn["status"] == "active"


def test_goals_status_math(session: Session, user: User) -> None:
    deadline = date(today().year + 1, today().month, today().day)
    goals.create_goal(session, user.id, "Laptop", "150000", deadline=deadline.isoformat())
    goals.create_goal(session, user.id, "Phone", "50000")
    goals.add_to_goal(session, user.id, "laptop", "30000")

    status = {row["name"]: row for row in goals.goals_status(session, user.id)}

    laptop = status["Laptop"]
    assert laptop["saved"] == Decimal("30000.00")
    assert laptop["target"] == Decimal("150000.00")
    assert laptop["pct"] == Decimal("20.00")
    assert laptop["remaining"] == Decimal("120000.00")
    assert laptop["deadline"] == deadline
    assert laptop["monthly_needed"] == Decimal("10000.00")

    phone = status["Phone"]
    assert phone["saved"] == Decimal("0.00")
    assert phone["monthly_needed"] is None
    assert phone["status"] == "active"


def test_goals_status_without_remaining_needs_nothing_monthly(
    session: Session, user: User
) -> None:
    goals.create_goal(session, user.id, "Phone", "50000", deadline="2027-12-31")
    goals.add_to_goal(session, user.id, "phone", "60000")

    row = goals.goals_status(session, user.id)[0]

    assert row["saved"] == Decimal("60000.00")
    assert row["remaining"] == Decimal("0.00")
    assert row["monthly_needed"] is None
    assert row["status"] == "achieved"
    assert row["pct"] == Decimal("120.00")


def test_goals_status_with_no_goals(session: Session, user: User) -> None:
    assert goals.goals_status(session, user.id) == []
