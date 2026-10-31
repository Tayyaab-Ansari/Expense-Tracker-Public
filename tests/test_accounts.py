"""Tests for the account service."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import DebtEntry, User
from app.services import accounts
from app.services import transactions as tx

DAY = date(2026, 10, 15)


def balance_of(session: Session, user_id: int, name: str) -> Decimal:
    """Current balance of one named account."""
    return next(row["balance"] for row in accounts.get_balances(session, user_id) if row["name"] == name)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("jazz", "JazzCash"),
        ("jazz cash", "JazzCash"),
        ("easy paisa", "Easypaisa"),
        ("easypaisa", "Easypaisa"),
        ("card", "Bank"),
        ("bank card", "Bank"),
        ("debit card", "Bank"),
        ("atm", "Bank"),
        ("naqd", "Cash"),
        ("cash", "Cash"),
        ("CASH", "Cash"),
        ("  Bank  ", "Bank"),
    ],
)
def test_resolve_account_aliases(session: Session, user: User, given: str, expected: str) -> None:
    assert accounts.resolve_account(session, user.id, given).name == expected


def test_resolve_account_defaults_to_cash(session: Session, user: User) -> None:
    assert accounts.resolve_account(session, user.id, None).name == "Cash"
    assert accounts.resolve_account(session, user.id, "  ").name == "Cash"


def test_resolve_account_unknown_lists_valid_names(session: Session, user: User) -> None:
    with pytest.raises(ValueError, match="Cash"):
        accounts.resolve_account(session, user.id, "paypal")


def test_set_default_account(session: Session, user: User) -> None:
    result = accounts.set_default_account(session, user.id, "bank card")

    assert result["name"] == "Bank"
    assert result["is_default"] is True
    assert accounts.resolve_account(session, user.id, None).name == "Bank"
    assert session.get(User, user.id).default_account_id == result["id"]
    assert [row["name"] for row in accounts.get_balances(session, user.id) if row["is_default"]] == [
        "Bank"
    ]


def test_balances_after_expense_and_income(session: Session, user: User) -> None:
    cash = accounts.resolve_account(session, user.id, "cash")
    cash.opening_balance = Decimal("1000.00")
    session.flush()

    tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "date": DAY.isoformat()},
            {"type": "income", "amount": "500", "category": "salary", "date": DAY.isoformat()},
        ],
    )

    assert balance_of(session, user.id, "Cash") == Decimal("1350.00")
    assert balance_of(session, user.id, "Bank") == Decimal("0.00")


def test_balances_exclude_deleted_and_include_debts(session: Session, user: User) -> None:
    saved = tx.add_transactions(
        session, user.id, [{"amount": "99", "category": "food", "date": DAY.isoformat()}]
    )["saved"]
    tx.delete_transaction(session, user.id, saved[0]["id"])

    cash = accounts.resolve_account(session, user.id, "cash")
    session.add_all(
        [
            DebtEntry(
                user_id=user.id,
                person="Ali",
                kind="lent",
                amount=Decimal("200.00"),
                account_id=cash.id,
                occurred_on=DAY,
            ),
            DebtEntry(
                user_id=user.id,
                person="Ali",
                kind="repaid_to_me",
                amount=Decimal("50.00"),
                account_id=cash.id,
                occurred_on=DAY,
            ),
            DebtEntry(
                user_id=user.id,
                person="Sara",
                kind="borrowed",
                amount=Decimal("300.00"),
                account_id=cash.id,
                occurred_on=DAY,
            ),
        ]
    )
    session.flush()

    assert balance_of(session, user.id, "Cash") == Decimal("150.00")


def test_set_current_balance(session: Session, user: User) -> None:
    tx.add_transactions(
        session, user.id, [{"amount": "150", "category": "food", "date": DAY.isoformat()}]
    )

    result = accounts.set_current_balance(session, user.id, "cash", Decimal("2000.00"))

    assert result["balance"] == Decimal("2000.00")
    assert balance_of(session, user.id, "Cash") == Decimal("2000.00")
    assert accounts.resolve_account(session, user.id, "cash").opening_balance == Decimal("2150.00")


def test_list_accounts_puts_default_first(session: Session, user: User) -> None:
    names = [account.name for account in accounts.list_accounts(session, user.id)]

    assert names[0] == "Cash"
    assert sorted(names) == ["Bank", "Cash", "Easypaisa", "JazzCash"]
