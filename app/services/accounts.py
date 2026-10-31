"""Account service: list, create and compute account balances."""

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import MONEY, Account, DebtEntry, Transaction, User

ACCOUNT_ALIASES = {
    "jazz cash": "JazzCash",
    "jazz": "JazzCash",
    "easy paisa": "Easypaisa",
    "easypaisa": "Easypaisa",
    "card": "Bank",
    "bank card": "Bank",
    "debit card": "Bank",
    "atm": "Bank",
    "naqd": "Cash",
    "cash": "Cash",
}


def list_accounts(session: Session, user_id: int) -> list[Account]:
    """List a user's accounts, default first."""
    query = (
        select(Account)
        .where(Account.user_id == user_id)
        .order_by(Account.is_default.desc(), Account.name)
    )
    return list(session.scalars(query))


def resolve_account(session: Session, user_id: int, name: str | None) -> Account:
    """Resolve a free-text account name, defaulting to the user's default account."""
    accounts = list_accounts(session, user_id)
    if not accounts:
        raise ValueError("No accounts exist for this user")

    if not name or not name.strip():
        for account in accounts:
            if account.is_default:
                return account
        return accounts[0]

    wanted = name.strip().lower()
    wanted = ACCOUNT_ALIASES.get(wanted, wanted).lower()
    for account in accounts:
        if account.name.lower() == wanted:
            return account

    valid = ", ".join(account.name for account in accounts)
    raise ValueError(f"Unknown account: {name!r}. Valid accounts: {valid}")


def set_default_account(session: Session, user_id: int, name: str) -> dict:
    """Make one account the default for new transactions."""
    account = resolve_account(session, user_id, name)
    for other in list_accounts(session, user_id):
        other.is_default = other.id == account.id
    user = session.get(User, user_id)
    if user is not None:
        user.default_account_id = account.id
    session.flush()
    return {"id": account.id, "name": account.name, "is_default": True}


def get_balances(session: Session, user_id: int) -> list[dict]:
    """Current balance of every account, excluding soft-deleted transactions."""
    def total(condition, column) -> object:
        """Sum a money column for the rows matching a condition."""
        return func.coalesce(func.sum(case((condition, column), else_=0)), 0, type_=MONEY)

    transactions = (
        select(
            Transaction.account_id.label("account_id"),
            total(Transaction.type == "income", Transaction.amount).label("income"),
            total(Transaction.type == "expense", Transaction.amount).label("expense"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.account_id.is_not(None),
        )
        .group_by(Transaction.account_id)
        .subquery()
    )
    debts = (
        select(
            DebtEntry.account_id.label("account_id"),
            total(DebtEntry.kind == "lent", DebtEntry.amount).label("lent"),
            total(DebtEntry.kind == "borrowed", DebtEntry.amount).label("borrowed"),
            total(DebtEntry.kind == "repaid_to_me", DebtEntry.amount).label("repaid_to_me"),
            total(DebtEntry.kind == "repaid_by_me", DebtEntry.amount).label("repaid_by_me"),
        )
        .where(DebtEntry.user_id == user_id, DebtEntry.account_id.is_not(None))
        .group_by(DebtEntry.account_id)
        .subquery()
    )

    rows = session.execute(
        select(
            Account.id,
            Account.name,
            Account.is_default,
            Account.opening_balance,
            func.coalesce(transactions.c.income, 0, type_=MONEY),
            func.coalesce(transactions.c.expense, 0, type_=MONEY),
            func.coalesce(debts.c.lent, 0, type_=MONEY),
            func.coalesce(debts.c.borrowed, 0, type_=MONEY),
            func.coalesce(debts.c.repaid_to_me, 0, type_=MONEY),
            func.coalesce(debts.c.repaid_by_me, 0, type_=MONEY),
        )
        .outerjoin(transactions, transactions.c.account_id == Account.id)
        .outerjoin(debts, debts.c.account_id == Account.id)
        .where(Account.user_id == user_id)
        .order_by(Account.is_default.desc(), Account.name)
    ).all()

    return [
        {
            "id": account_id,
            "name": name,
            "balance": Decimal(opening)
            + income
            - expense
            - lent
            + borrowed
            + repaid_to_me
            - repaid_by_me,
            "is_default": is_default,
        }
        for (
            account_id,
            name,
            is_default,
            opening,
            income,
            expense,
            lent,
            borrowed,
            repaid_to_me,
            repaid_by_me,
        ) in rows
    ]


def set_current_balance(session: Session, user_id: int, name: str, balance: Decimal) -> dict:
    """Shift an account's opening balance so its current balance matches the given value."""
    account = resolve_account(session, user_id, name)
    current = next(row["balance"] for row in get_balances(session, user_id) if row["id"] == account.id)
    account.opening_balance = account.opening_balance + (balance - current)
    session.flush()
    return {"id": account.id, "name": account.name, "balance": balance}
