"""Transaction service: add, edit, soft delete and list transactions."""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services.accounts import resolve_account
from app.services.budgets import warning_for
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


def _budget_warnings(
    session: Session, user_id: int, transactions: list[Transaction]
) -> list[str]:
    """Deduplicated budget warnings for the expense categories just saved."""
    seen: set[tuple[int, date]] = set()
    warnings: list[str] = []
    for transaction in transactions:
        if transaction.type != "expense" or transaction.category_id is None:
            continue
        key = (transaction.category_id, transaction.occurred_on)
        if key in seen:
            continue
        seen.add(key)
        warning = warning_for(session, user_id, transaction.category_id, transaction.occurred_on)
        if warning and warning not in warnings:
            warnings.append(warning)
    return warnings


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
    result = {"saved": saved, "confirmation_text": "\n".join(lines)}

    warnings = _budget_warnings(session, user_id, transactions)
    if warnings:
        result["budget_warnings"] = warnings
    return result


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


def _find(session: Session, user_id: int, ref: int | str) -> Transaction:
    """Resolve a transaction id or the keyword 'last' to a live transaction."""
    if isinstance(ref, str) and ref.strip().lower() == "last":
        transaction = session.scalar(
            select(Transaction)
            .where(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(1)
        )
        if transaction is None:
            raise ValueError("No transactions to work with yet")
        return transaction

    try:
        tx_id = int(ref)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid transaction reference: {ref!r}") from exc

    transaction = get_transaction(session, user_id, tx_id)
    if transaction is None:
        raise ValueError(f"Transaction #{tx_id} not found")
    return transaction


def update_transaction(
    session: Session,
    user_id: int,
    ref: int | str,
    amount: str | None = None,
    category: str | None = None,
    account: str | None = None,
    description: str | None = None,
    date: str | None = None,
    type: str | None = None,
) -> dict:
    """Update fields of an existing transaction and report the before and after state."""
    transaction = _find(session, user_id, ref)
    before = _as_dict(transaction)

    if type is not None:
        kind = type.strip().lower()
        if kind not in ICONS:
            raise ValueError(f"Unknown transaction type: {kind!r}, expected expense or income")
        transaction.type = kind
        # the old category belongs to the old kind, so re-resolve it for the new one
        resolved = resolve_category(session, user_id, category or before["category"], kind)
        transaction.category_id = resolved.id
    elif category is not None:
        transaction.category_id = resolve_category(
            session, user_id, category, transaction.type
        ).id

    if amount is not None:
        transaction.amount = to_decimal(amount, positive=True)
    if account is not None:
        transaction.account_id = resolve_account(session, user_id, account).id
    if description is not None:
        transaction.description = description.strip() or None
    if date is not None:
        transaction.occurred_on = parse_date(date)

    transaction.updated_at = datetime.now(timezone.utc)
    session.flush()
    session.refresh(transaction)

    after = _as_dict(transaction)
    parts = [
        f"✏️ #{after['id']} updated: {after['category'] or 'Uncategorised'}",
        format_rs(after["amount"]),
        after["account"] or "No account",
        human_date(after["date"]),
    ]
    return {"before": before, "after": after, "confirmation_text": " · ".join(parts)}


def _soft_delete(session: Session, user_id: int, ref: int | str, prefix: str) -> dict:
    """Soft delete a transaction and build its confirmation line."""
    transaction = _find(session, user_id, ref)
    deleted = _as_dict(transaction)
    transaction.deleted_at = datetime.now(timezone.utc)
    session.flush()

    category = deleted["category"] or "Uncategorised"
    text = f"{prefix} #{deleted['id']} {category} · {format_rs(deleted['amount'])}"
    return {"deleted": deleted, "confirmation_text": text}


def delete_transaction(session: Session, user_id: int, ref: int | str) -> dict:
    """Soft delete a transaction by id or the keyword 'last'."""
    return _soft_delete(session, user_id, ref, "🗑️ Deleted")


def undo_last(session: Session, user_id: int) -> dict:
    """Soft delete the most recently created transaction."""
    return _soft_delete(session, user_id, "last", "↩️ Undone")
