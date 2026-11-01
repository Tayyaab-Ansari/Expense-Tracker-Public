"""Savings goal service: goals and their contributions."""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MONEY, GoalContribution, SavingsGoal
from app.utils.dates import parse_date, today
from app.utils.money import format_rs, to_decimal

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")
ACTIVE = "active"
ACHIEVED = "achieved"


def _saved_expression():
    """SQL expression for the amount saved towards a goal."""
    return func.coalesce(func.sum(GoalContribution.amount), 0, type_=MONEY)


def _pct(saved: Decimal, target: Decimal) -> Decimal:
    """Share of a goal already saved."""
    if target == 0:
        return ZERO
    return (saved / target * 100).quantize(CENTS)


def _find_goal(session: Session, user_id: int, name: str) -> SavingsGoal:
    """Match a goal by name, case-insensitively, by prefix then by substring."""
    wanted = (name or "").strip().lower()
    if not wanted:
        raise ValueError("Goal name must not be empty")

    goals = list(
        session.scalars(
            select(SavingsGoal).where(SavingsGoal.user_id == user_id).order_by(SavingsGoal.name)
        )
    )
    for match in (
        lambda goal: goal.name.lower() == wanted,
        lambda goal: goal.name.lower().startswith(wanted),
        lambda goal: wanted in goal.name.lower(),
    ):
        found = [goal for goal in goals if match(goal)]
        if found:
            return found[0]
    raise ValueError(f"No savings goal matching {name!r}")


def _saved_for(session: Session, goal_id: int) -> Decimal:
    """Amount saved towards one goal."""
    return session.scalar(
        select(_saved_expression()).where(GoalContribution.goal_id == goal_id)
    )


def create_goal(
    session: Session,
    user_id: int,
    name: str,
    target_amount: str | Decimal,
    deadline: str | None = None,
) -> dict:
    """Create a savings goal."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Goal name must not be empty")

    existing = session.scalar(
        select(SavingsGoal).where(
            SavingsGoal.user_id == user_id, func.lower(SavingsGoal.name) == clean.lower()
        )
    )
    if existing is not None:
        raise ValueError(f"A goal called {existing.name} already exists")

    goal = SavingsGoal(
        user_id=user_id,
        name=clean,
        target_amount=to_decimal(target_amount, positive=True),
        deadline=parse_date(deadline) if deadline else None,
        status=ACTIVE,
    )
    session.add(goal)
    session.flush()

    return {
        "id": goal.id,
        "name": goal.name,
        "target": goal.target_amount,
        "target_display": format_rs(goal.target_amount),
        "deadline": goal.deadline,
        "status": goal.status,
    }


def add_to_goal(
    session: Session,
    user_id: int,
    name: str,
    amount: str | Decimal,
    date: str | None = None,
) -> dict:
    """Add to or withdraw from a goal, updating its status when the target is met."""
    goal = _find_goal(session, user_id, name)
    value = to_decimal(amount)
    if value == 0:
        raise ValueError("Amount must not be zero")

    session.add(
        GoalContribution(goal_id=goal.id, amount=value, occurred_on=parse_date(date))
    )
    session.flush()

    saved = _saved_for(session, goal.id)
    goal.status = ACHIEVED if saved >= goal.target_amount else ACTIVE
    session.flush()

    pct = _pct(saved, goal.target_amount)
    return {
        "name": goal.name,
        "amount": value,
        "saved": saved,
        "target": goal.target_amount,
        "pct": pct,
        "status": goal.status,
        "confirmation_text": (
            f"🎯 {goal.name}: {format_rs(saved)} / {format_rs(goal.target_amount)} ({pct:.0f}%)"
        ),
    }


def _months_left(deadline: date, on_date: date) -> int:
    """Whole months remaining until a deadline, never less than one."""
    months = (deadline.year - on_date.year) * 12 + (deadline.month - on_date.month)
    if deadline.day < on_date.day:
        months -= 1
    return max(1, months)


def goals_status(session: Session, user_id: int) -> list[dict]:
    """Every goal with progress and the monthly saving needed to reach it."""
    rows = session.execute(
        select(
            SavingsGoal.name,
            SavingsGoal.target_amount,
            SavingsGoal.deadline,
            SavingsGoal.status,
            _saved_expression().label("saved"),
        )
        .outerjoin(GoalContribution, GoalContribution.goal_id == SavingsGoal.id)
        .where(SavingsGoal.user_id == user_id)
        .group_by(SavingsGoal.id)
        .order_by(SavingsGoal.name)
    ).all()

    on_date = today()
    status = []
    for row in rows:
        remaining = max(ZERO, row.target_amount - row.saved)
        monthly_needed = None
        if row.deadline is not None and remaining > 0:
            monthly_needed = (remaining / _months_left(row.deadline, on_date)).quantize(CENTS)
        status.append(
            {
                "name": row.name,
                "saved": row.saved,
                "target": row.target_amount,
                "pct": _pct(row.saved, row.target_amount),
                "remaining": remaining,
                "deadline": row.deadline,
                "monthly_needed": monthly_needed,
                "status": row.status,
            }
        )
    return status
