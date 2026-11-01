"""Analytics service: totals, breakdowns and period comparisons."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models import MONEY, Account, Category, Transaction
from app.services.accounts import resolve_account
from app.utils.dates import month_bounds
from app.utils.money import pct_change, to_decimal

MAX_SEARCH_LIMIT = 20
MAX_COMPARE_CATEGORIES = 8
MAX_MOVERS = 3
AT_RISK_PCT = Decimal("80")
CENTS = Decimal("0.01")
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


def _expense_by_category(
    session: Session, user_id: int, start: date, end: date
) -> dict[str, Decimal]:
    """Expense total per category name for a period."""
    summary = spending_summary(session, user_id, start, end)
    return {row["category"]: row["total"] for row in summary["by_category"]}


def _category_diffs(first: dict[str, Decimal], second: dict[str, Decimal]) -> list[dict]:
    """Per-category movement from the first period to the second."""
    diffs = []
    for name in first.keys() | second.keys():
        before = first.get(name, ZERO)
        after = second.get(name, ZERO)
        diffs.append(
            {
                "category": name,
                "a": before,
                "b": after,
                "diff": after - before,
                "pct": pct_change(before, after),
            }
        )
    return diffs


def _previous_month(year: int, month: int) -> tuple[int, int]:
    """Year and month before the given one."""
    previous = date(year, month, 1) - timedelta(days=1)
    return previous.year, previous.month


def compare_months(
    session: Session, user_id: int, a: tuple[int, int], b: tuple[int, int]
) -> dict:
    """Compare two months, reporting the movement from month a to month b."""
    a_start, a_end = month_bounds(*a)
    b_start, b_end = month_bounds(*b)

    totals = {}
    for key, (start, end) in {"a": (a_start, a_end), "b": (b_start, b_end)}.items():
        expense = spending_summary(session, user_id, start, end)["total"]
        income = spending_summary(session, user_id, start, end, type="income")["total"]
        totals[key] = {"income": income, "expense": expense, "net": income - expense}

    diffs = _category_diffs(
        _expense_by_category(session, user_id, a_start, a_end),
        _expense_by_category(session, user_id, b_start, b_end),
    )
    diffs.sort(key=lambda row: abs(row["diff"]), reverse=True)

    return {
        "a": totals["a"],
        "b": totals["b"],
        "expense_change_pct": pct_change(totals["a"]["expense"], totals["b"]["expense"]),
        "categories": diffs[:MAX_COMPARE_CATEGORIES],
    }


def insights_data(session: Session, user_id: int, year: int, month: int, today: date) -> dict:
    """Spending insights for a month, compared fairly with the same point last month."""
    # imported here because budgets builds on this module
    from app.services.budgets import budget_status

    first, last = month_bounds(year, month)
    is_current = (year, month) == (today.year, today.month)
    end = min(max(today, first), last) if is_current else last

    previous_year, previous_month = _previous_month(year, month)
    previous_first, previous_last = month_bounds(previous_year, previous_month)
    previous_end = date(previous_year, previous_month, min(end.day, previous_last.day))

    spent_so_far = spending_summary(session, user_id, first, end)["total"]
    prev_same_point = spending_summary(session, user_id, previous_first, previous_end)["total"]
    daily_average = (spent_so_far / end.day).quantize(CENTS)

    diffs = _category_diffs(
        _expense_by_category(session, user_id, previous_first, previous_end),
        _expense_by_category(session, user_id, first, end),
    )
    rising = sorted(diffs, key=lambda row: row["diff"], reverse=True)
    biggest = top_expenses(session, user_id, first, end, n=1)

    return {
        "period_label": (
            f"{first.day}–{end.day} {first:%b %Y}" if end != last else f"{first:%b %Y}"
        ),
        "spent_so_far": spent_so_far,
        "prev_same_point": prev_same_point,
        "change_pct": pct_change(prev_same_point, spent_so_far),
        "daily_average": daily_average,
        "projected_month_total": (daily_average * last.day).quantize(CENTS) if is_current else None,
        "top_increases": [row for row in rising if row["diff"] > 0][:MAX_MOVERS],
        "top_decreases": [row for row in reversed(rising) if row["diff"] < 0][:MAX_MOVERS],
        "biggest_expense": biggest[0] if biggest else None,
        "budgets_at_risk": [
            item
            for item in budget_status(session, user_id, year, month)["items"]
            if item["pct"] >= AT_RISK_PCT
        ],
    }
