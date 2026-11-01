"""Debt service: record lending, borrowing and repayments."""

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import MONEY, Account, DebtEntry
from app.services.accounts import resolve_account
from app.utils.dates import human_date, parse_date
from app.utils.money import format_rs, to_decimal

KINDS = ("lent", "borrowed", "repaid_to_me", "repaid_by_me")
ZERO = Decimal("0.00")


def _normalise_person(person: str) -> str:
    """Title-case a person's name."""
    clean = (person or "").strip()
    if not clean:
        raise ValueError("Person name must not be empty")
    return clean.title()


def _net_expression():
    """SQL expression for a person's net balance."""
    signs = {"lent": 1, "repaid_to_me": -1, "borrowed": -1, "repaid_by_me": 1}
    return func.coalesce(
        func.sum(
            case(
                *((DebtEntry.kind == kind, DebtEntry.amount * sign) for kind, sign in signs.items()),
                else_=0,
            )
        ),
        0,
        type_=MONEY,
    )


def _status(net: Decimal) -> str:
    """How a net balance reads to the owner."""
    if net > 0:
        return "owes you"
    if net < 0:
        return "you owe"
    return "settled"


def _balance_text(person: str, net: Decimal) -> str:
    """Standing balance with a person, phrased for a repayment confirmation."""
    if net > 0:
        return f"{person} still owes you {format_rs(net)}"
    if net < 0:
        return f"You still owe {person} {format_rs(-net)}"
    return f"Settled with {person}"


def person_balance(session: Session, user_id: int, person: str) -> Decimal:
    """Net balance with one person: positive when they owe the owner."""
    return session.scalar(
        select(_net_expression()).where(
            DebtEntry.user_id == user_id, DebtEntry.person == person
        )
    )


def _confirmation_line(entry: DebtEntry, account_name: str | None, net: Decimal) -> str:
    """Confirmation line for a recorded debt entry."""
    amount = format_rs(entry.amount)
    where = account_name or "No account"
    when = human_date(entry.occurred_on)

    if entry.kind == "lent":
        return f"🤝 Lent {amount} to {entry.person} · {where} · {when}"
    if entry.kind == "borrowed":
        return f"🤝 Borrowed {amount} from {entry.person} · {where} · {when}"
    if entry.kind == "repaid_to_me":
        return f"✅ {entry.person} paid back {amount} · {_balance_text(entry.person, net)}"
    return f"✅ Paid back {amount} to {entry.person} · {_balance_text(entry.person, net)}"


def record_debt(
    session: Session,
    user_id: int,
    person: str,
    kind: str,
    amount: str | Decimal,
    account: str | None = None,
    note: str | None = None,
    date: str | None = None,
) -> dict:
    """Record money lent, borrowed or repaid and report the standing balance."""
    if kind not in KINDS:
        raise ValueError(f"Unknown debt kind: {kind!r}, expected one of {', '.join(KINDS)}")

    name = _normalise_person(person)
    value = to_decimal(amount, positive=True)
    resolved = resolve_account(session, user_id, account)

    entry = DebtEntry(
        user_id=user_id,
        person=name,
        kind=kind,
        amount=value,
        account_id=resolved.id,
        note=(note or "").strip() or None,
        occurred_on=parse_date(date),
    )
    session.add(entry)
    session.flush()

    net = person_balance(session, user_id, name)
    return {
        "saved": {
            "id": entry.id,
            "person": name,
            "kind": kind,
            "amount": value,
            "account": resolved.name,
            "note": entry.note,
            "date": entry.occurred_on,
        },
        "confirmation_text": _confirmation_line(entry, resolved.name, net),
        "person_balance": net,
    }


def debt_summary(session: Session, user_id: int, person: str | None = None) -> dict:
    """Net balance per person, with totals in each direction."""
    query = (
        select(DebtEntry.person, _net_expression().label("net"))
        .where(DebtEntry.user_id == user_id)
        .group_by(DebtEntry.person)
        .order_by(DebtEntry.person)
    )
    if person:
        query = query.where(DebtEntry.person == _normalise_person(person))

    people = [
        {"person": row.person, "net": row.net, "status": _status(row.net)}
        for row in session.execute(query).all()
    ]
    return {
        "people": people,
        "total_owed_to_you": sum((row["net"] for row in people if row["net"] > 0), ZERO),
        "total_you_owe": sum((-row["net"] for row in people if row["net"] < 0), ZERO),
    }


def debt_history(session: Session, user_id: int, person: str) -> list[dict]:
    """Every debt entry with one person, oldest first."""
    rows = session.execute(
        select(
            DebtEntry.id,
            DebtEntry.kind,
            DebtEntry.amount,
            Account.name.label("account"),
            DebtEntry.note,
            DebtEntry.occurred_on,
        )
        .outerjoin(Account, Account.id == DebtEntry.account_id)
        .where(DebtEntry.user_id == user_id, DebtEntry.person == _normalise_person(person))
        .order_by(DebtEntry.occurred_on, DebtEntry.id)
    ).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "amount": row.amount,
            "account": row.account,
            "note": row.note,
            "date": row.occurred_on,
        }
        for row in rows
    ]
