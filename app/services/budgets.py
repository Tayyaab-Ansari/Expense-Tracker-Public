"""Budget service: set budgets and report spend against them."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Budget, Category
from app.services.analytics import spending_summary
from app.services.categories import list_categories
from app.utils.dates import month_bounds
from app.utils.money import format_rs, to_decimal

ZERO = Decimal("0.00")
WARN_AT = Decimal("80")
EXCEEDED_AT = Decimal("100")


def _expense_category(session: Session, user_id: int, name: str) -> Category:
    """Resolve an expense category by name, rejecting income or unknown names."""
    wanted = (name or "").strip().lower()
    if not wanted:
        raise ValueError("Category name must not be empty")

    expense = list_categories(session, user_id, "expense")
    for category in expense:
        if category.name.lower() == wanted:
            return category
    for category in expense:
        if category.name.lower().startswith(wanted):
            return category

    for category in list_categories(session, user_id, "income"):
        if category.name.lower() == wanted or category.name.lower().startswith(wanted):
            raise ValueError(f"{category.name} is an income category, budgets are for expenses")
    raise ValueError(f"Unknown expense category: {name!r}")


def _pct(spent: Decimal, budget: Decimal) -> Decimal:
    """Share of a budget already spent."""
    if budget == 0:
        return ZERO
    return (spent / budget * 100).quantize(Decimal("0.01"))


def set_budget(session: Session, user_id: int, category: str, amount: str | Decimal) -> dict:
    """Create or update the monthly budget of an expense category."""
    resolved = _expense_category(session, user_id, category)
    value = to_decimal(amount, positive=True)

    budget = session.scalar(
        select(Budget).where(Budget.user_id == user_id, Budget.category_id == resolved.id)
    )
    if budget is None:
        budget = Budget(user_id=user_id, category_id=resolved.id, amount=value)
        session.add(budget)
    else:
        budget.amount = value
    session.flush()
    return {"category": resolved.name, "amount": value, "amount_display": format_rs(value)}


def remove_budget(session: Session, user_id: int, category: str) -> dict:
    """Delete the budget of an expense category."""
    resolved = _expense_category(session, user_id, category)
    budget = session.scalar(
        select(Budget).where(Budget.user_id == user_id, Budget.category_id == resolved.id)
    )
    if budget is None:
        raise ValueError(f"No budget set for {resolved.name}")

    session.delete(budget)
    session.flush()
    return {"category": resolved.name, "removed": True}


def budget_status(session: Session, user_id: int, year: int, month: int) -> dict:
    """Budgets with spend for one month, most used first."""
    start, end = month_bounds(year, month)
    rows = session.execute(
        select(Category.name, Budget.amount)
        .join(Category, Category.id == Budget.category_id)
        .where(Budget.user_id == user_id)
    ).all()

    summary = spending_summary(session, user_id, start, end)
    spent_by_category = {row["category"]: row["total"] for row in summary["by_category"]}

    items = []
    for name, amount in rows:
        spent = spent_by_category.get(name, ZERO)
        items.append(
            {
                "category": name,
                "budget": amount,
                "spent": spent,
                "remaining": amount - spent,
                "pct": _pct(spent, amount),
            }
        )
    items.sort(key=lambda item: item["pct"], reverse=True)

    return {
        "month": f"{year:04d}-{month:02d}",
        "items": items,
        "total_budget": sum((item["budget"] for item in items), ZERO),
        "total_spent": sum((item["spent"] for item in items), ZERO),
    }


def warning_for(
    session: Session, user_id: int, category_id: int, on_date: date
) -> str | None:
    """Budget warning for a category after an expense, or None when it is not due."""
    row = session.execute(
        select(Category.name, Budget.amount)
        .join(Budget, Budget.category_id == Category.id)
        .where(Budget.user_id == user_id, Budget.category_id == category_id)
    ).first()
    if row is None:
        return None

    name, budget = row
    start, end = month_bounds(on_date.year, on_date.month)
    spent = spending_summary(session, user_id, start, end, category=name)["total"]
    pct = _pct(spent, budget)

    if pct >= EXCEEDED_AT:
        return (
            f"🚨 {name} budget exceeded: {format_rs(spent)} / {format_rs(budget)} ({pct:.0f}%)"
        )
    if pct >= WARN_AT:
        return f"⚠️ {name}: {format_rs(spent)} / {format_rs(budget)} used ({pct:.0f}%)"
    return None
