"""Analytics service: totals, breakdowns and period comparisons."""

from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models import MONEY, Account, Category, Transaction
from app.services.accounts import resolve_account
from app.utils.money import to_decimal

MAX_SEARCH_LIMIT = 20
ZERO = Decimal("0.00")


def _category_ids(session: Session, user_id: int, name: str, kind: str | None) -> list[int]:
    """Ids of a user's categories matching a name exactly, else by prefix."""
    query = select(Category.id, Category.name).where(Category.user_id == user_id)
    if kind is not None:
        query = query.where(Category.kind == kind)
    rows = session.execute(query).all()

    wanted = name.strip().lower()
    exact = [row.id for row in rows if row.name.lower() == wanted]
    if exact:
        return exact
    prefixed = [row.id for row in rows if row.name.lower().startswith(wanted)]
    if prefixed:
        return prefixed
    raise ValueError(f"Unknown category: {name!r}")


def _filters(
    session: Session,
    user_id: int,
    *,
    type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[ColumnElement[bool]]:
    """Build the shared transaction filters, always excluding soft-deleted rows."""
    clauses: list[ColumnElement[bool]] = [
        Transaction.user_id == user_id,
        Transaction.deleted_at.is_(None),
    ]
    if type is not None:
        clauses.append(Transaction.type == type)
    if start is not None:
        clauses.append(Transaction.occurred_on >= start)
    if end is not None:
        clauses.append(Transaction.occurred_on <= end)
    if category:
        clauses.append(Transaction.category_id.in_(_category_ids(session, user_id, category, type)))
    if account:
        clauses.append(Transaction.account_id == resolve_account(session, user_id, account).id)
    return clauses


def _total(column) -> ColumnElement:
    """Sum of a money column, zero when there are no rows."""
    return func.coalesce(func.sum(column), 0, type_=MONEY)


def spending_summary(
    session: Session,
    user_id: int,
    start: date,
    end: date,
    type: str = "expense",
    category: str | None = None,
    account: str | None = None,
) -> dict:
    """Totals for a period with per-category and per-account breakdowns."""
    clauses = _filters(
        session, user_id, type=type, category=category, account=account, start=start, end=end
    )

    total, count = session.execute(
        select(_total(Transaction.amount), func.count(Transaction.id)).where(*clauses)
    ).one()

    by_category = session.execute(
        select(
            Category.name,
            _total(Transaction.amount).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .join(Category, Category.id == Transaction.category_id)
        .where(*clauses)
        .group_by(Category.name)
        .order_by(_total(Transaction.amount).desc())
    ).all()

    by_account = session.execute(
        select(Account.name, _total(Transaction.amount).label("total"))
        .join(Account, Account.id == Transaction.account_id)
        .where(*clauses)
        .group_by(Account.name)
        .order_by(_total(Transaction.amount).desc())
    ).all()

    return {
        "start": start,
        "end": end,
        "type": type,
        "total": total,
        "count": count,
        "by_category": [
            {
                "category": row.name,
                "total": row.total,
                "count": row.count,
                "pct": (row.total / total * 100).quantize(Decimal("0.01")) if total else ZERO,
            }
            for row in by_category
        ],
        "by_account": [{"account": row.name, "total": row.total} for row in by_account],
    }


def search_transactions(
    session: Session,
    user_id: int,
    text: str | None = None,
    min_amount: str | None = None,
    max_amount: str | None = None,
    category: str | None = None,
    account: str | None = None,
    type: str | None = None,
    start: date | None = None,
    end: date | None = None,
    sort: str = "newest",
    limit: int = 10,
) -> dict:
    """Search transactions by text, amount range, category, account, type and period."""
    clauses = _filters(
        session, user_id, type=type, category=category, account=account, start=start, end=end
    )
    if text:
        clauses.append(Transaction.description.ilike(f"%{text.strip()}%"))
    if min_amount is not None:
        clauses.append(Transaction.amount >= to_decimal(min_amount))
    if max_amount is not None:
        clauses.append(Transaction.amount <= to_decimal(max_amount))

    orderings = {
        "newest": (Transaction.occurred_on.desc(), Transaction.id.desc()),
        "oldest": (Transaction.occurred_on.asc(), Transaction.id.asc()),
        "largest": (Transaction.amount.desc(), Transaction.id.desc()),
    }
    if sort not in orderings:
        raise ValueError(f"Unknown sort: {sort!r}, expected one of {', '.join(orderings)}")

    count_total, total_amount = session.execute(
        select(func.count(Transaction.id), _total(Transaction.amount)).where(*clauses)
    ).one()

    rows = session.execute(
        select(
            Transaction.id,
            Transaction.occurred_on,
            Transaction.type,
            Transaction.amount,
            Category.name.label("category"),
            Account.name.label("account"),
            Transaction.description,
        )
        .outerjoin(Category, Category.id == Transaction.category_id)
        .outerjoin(Account, Account.id == Transaction.account_id)
        .where(*clauses)
        .order_by(*orderings[sort])
        .limit(max(1, min(limit, MAX_SEARCH_LIMIT)))
    ).all()

    return {
        "count_total": count_total,
        "items": [
            {
                "id": row.id,
                "date": row.occurred_on,
                "type": row.type,
                "amount": row.amount,
                "category": row.category,
                "account": row.account,
                "description": row.description,
            }
            for row in rows
        ],
        "total_amount": total_amount,
    }


def top_expenses(session: Session, user_id: int, start: date, end: date, n: int = 5) -> list[dict]:
    """Largest expenses within a period."""
    return search_transactions(
        session, user_id, type="expense", start=start, end=end, sort="largest", limit=n
    )["items"]


def daily_totals(
    session: Session, user_id: int, start: date, end: date, type: str = "expense"
) -> dict[date, Decimal]:
    """Total per day for a period, including only days that have transactions."""
    rows = session.execute(
        select(Transaction.occurred_on, _total(Transaction.amount))
        .where(*_filters(session, user_id, type=type, start=start, end=end))
        .group_by(Transaction.occurred_on)
        .order_by(Transaction.occurred_on)
    ).all()
    return {occurred_on: total for occurred_on, total in rows}
