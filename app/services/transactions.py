"""Transaction service: add, edit, soft delete and list transactions."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services.accounts import resolve_account
from app.services.categories import resolve_category
from app.utils.dates import human_date, parse_date
from app.utils.money import format_rs, to_decimal

ICONS = {"expense": "✅", "income": "💰"}


def _as_dict(transaction: Transaction) -> dict:
    """Shape a transaction for service callers."""
    return {
        "id": transaction.id,
        "type": transaction.type,
        "amount": transaction.amount,
        "category": transaction.category.name if transaction.category else None,
        "account": transaction.account.name if transaction.account else None,
        "description": transaction.description,
        "date": transaction.occurred_on,
    }


def _confirmation_line(entry: dict) -> str:
    """Single WhatsApp confirmation line for a saved transaction."""
    parts = [
        f"{ICONS[entry['type']]} #{entry['id']} {entry['category'] or 'Uncategorised'}",
        format_rs(entry["amount"]),
        entry["account"] or "No account",
        human_date(entry["date"]),
    ]
    line = " · ".join(parts)
    if entry["description"]:
        line += f" — {entry['description']}"
    return line


def add_transactions(
    session: Session,
    user_id: int,
    items: list[dict],
    source: str = "text",
    raw_text: str | None = None,
) -> dict:
    """Insert one or more transactions and return them with a confirmation message."""
    if not items:
        raise ValueError("No transactions to save")

    transactions: list[Transaction] = []
    for item in items:
        kind = (item.get("type") or "expense").strip().lower()
        if kind not in ICONS:
            raise ValueError(f"Unknown transaction type: {kind!r}, expected expense or income")

        amount = to_decimal(item.get("amount"), positive=True)
        category = resolve_category(session, user_id, item.get("category"), kind)
        account = resolve_account(session, user_id, item.get("account"))
        description = (item.get("description") or "").strip() or None
        transactions.append(
            Transaction(
                user_id=user_id,
                type=kind,
                amount=amount,
                category_id=category.id,
                account_id=account.id,
                description=description,
                occurred_on=parse_date(item.get("date")),
                source=source,
                raw_text=raw_text,
            )
        )

    session.add_all(transactions)
    session.flush()

    saved = [_as_dict(transaction) for transaction in transactions]
    lines = [_confirmation_line(entry) for entry in saved]
    # budget warnings added in P24
    return {"saved": saved, "confirmation_text": "\n".join(lines)}


def get_transaction(session: Session, user_id: int, tx_id: int) -> Transaction | None:
    """Fetch one live transaction belonging to the user."""
    return session.scalar(
        select(Transaction).where(
            Transaction.id == tx_id,
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
        )
    )


def recent(session: Session, user_id: int, limit: int = 5) -> list[dict]:
    """Most recently recorded transactions, newest first."""
    query = (
        select(Transaction)
        .where(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
        .order_by(Transaction.occurred_on.desc(), Transaction.id.desc())
        .limit(limit)
    )
    return [_as_dict(transaction) for transaction in session.scalars(query)]
