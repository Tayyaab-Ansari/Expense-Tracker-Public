"""Dispatch of LLM tool calls to services and JSON serialisation of results."""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services import accounts, analytics, budgets, categories
from app.services import transactions as tx
from app.utils.dates import parse_month, resolve_period, today
from app.utils.money import format_rs, to_decimal

logger = logging.getLogger(__name__)

DISPLAY_FIELDS = ("total", "amount", "balance")


def _period(args: dict) -> tuple[date, date]:
    """Resolve the period arguments of a tool call."""
    return resolve_period(args.get("period"), args.get("start"), args.get("end"))


def _add_transactions(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Record the transactions the LLM extracted."""
    return tx.add_transactions(
        session,
        user_id,
        args.get("items") or [],
        source=context.get("source", "text"),
        raw_text=context.get("raw_text"),
    )


def _update_transaction(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Update one transaction."""
    return tx.update_transaction(
        session,
        user_id,
        args["ref"],
        amount=args.get("amount"),
        category=args.get("category"),
        account=args.get("account"),
        description=args.get("description"),
        date=args.get("date"),
        type=args.get("type"),
    )


def _delete_transaction(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Soft delete one transaction."""
    return tx.delete_transaction(session, user_id, args["ref"])


def _undo_last(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Soft delete the most recent transaction."""
    return tx.undo_last(session, user_id)


def _spending_summary(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Summarise spending for a period."""
    start, end = _period(args)
    return analytics.spending_summary(
        session,
        user_id,
        start,
        end,
        type=args.get("type") or "expense",
        category=args.get("category"),
        account=args.get("account"),
    )


def _search_transactions(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Search transactions, bounding the period only when one was asked for."""
    start = end = None
    if args.get("period") or args.get("start") or args.get("end"):
        start, end = _period(args)
    return analytics.search_transactions(
        session,
        user_id,
        text=args.get("text"),
        min_amount=args.get("min_amount"),
        max_amount=args.get("max_amount"),
        category=args.get("category"),
        account=args.get("account"),
        type=args.get("type"),
        start=start,
        end=end,
        sort=args.get("sort") or "newest",
        limit=args.get("limit") or 10,
    )


def _get_balances(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Report every account balance."""
    return {"accounts": accounts.get_balances(session, user_id)}


def _set_account_balance(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Correct an account's current balance."""
    return accounts.set_current_balance(
        session, user_id, args["account"], to_decimal(args["balance"])
    )


def _set_default_account(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Change the default account."""
    return accounts.set_default_account(session, user_id, args["account"])


def _add_category(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Create a category."""
    return categories.add_category(session, user_id, args["name"], args["kind"])


def _set_budget(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Set the monthly budget of an expense category."""
    return budgets.set_budget(session, user_id, args["category"], args["amount"])


def _remove_budget(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Remove the budget of an expense category."""
    return budgets.remove_budget(session, user_id, args["category"])


def _budget_status(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Report budgets against spend for a month."""
    year, month = parse_month(args.get("month"))
    return budgets.budget_status(session, user_id, year, month)


def _compare_months(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Compare two months of income and spending."""
    a = parse_month(args.get("month_a") or "last_month")
    b = parse_month(args.get("month_b") or "this_month")
    result = analytics.compare_months(session, user_id, a, b)
    return {
        "label_a": f"{a[0]:04d}-{a[1]:02d}",
        "label_b": f"{b[0]:04d}-{b[1]:02d}",
        **result,
    }


def _get_insights(session: Session, user_id: int, args: dict, context: dict) -> dict:
    """Spending insights for a month."""
    year, month = parse_month(args.get("month"))
    return analytics.insights_data(session, user_id, year, month, today())


REGISTRY = {
    "add_transactions": _add_transactions,
    "update_transaction": _update_transaction,
    "delete_transaction": _delete_transaction,
    "undo_last": _undo_last,
    "spending_summary": _spending_summary,
    "search_transactions": _search_transactions,
    "get_balances": _get_balances,
    "set_account_balance": _set_account_balance,
    "set_default_account": _set_default_account,
    "add_category": _add_category,
    "set_budget": _set_budget,
    "remove_budget": _remove_budget,
    "budget_status": _budget_status,
    "compare_months": _compare_months,
    "get_insights": _get_insights,
}


def _serialise(value: Any) -> Any:
    """Convert Decimals and dates in a nested structure to JSON-friendly strings."""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    return value


def _with_displays(result: dict) -> dict:
    """Serialise a result and add formatted totals for the LLM to quote."""
    payload = _serialise(result)
    for field in DISPLAY_FIELDS:
        if isinstance(result.get(field), Decimal):
            payload[f"{field}_display"] = format_rs(result[field])
    return payload


def dispatch(
    session: Session,
    user_id: int,
    name: str,
    args: dict,
    context: dict | None = None,
) -> dict:
    """Run one LLM tool call and return a JSON-serialisable result or an error."""
    handler = REGISTRY.get(name)
    if handler is None:
        logger.warning("LLM asked for unknown tool %s", name)
        return {"error": f"Unknown tool: {name}"}

    try:
        result = handler(session, user_id, args or {}, context or {})
    except (ValueError, KeyError, TypeError) as exc:
        logger.info("Tool %s rejected the arguments: %s", name, exc)
        message = exc.args[0] if exc.args else str(exc)
        return {"error": f"Missing argument: {message}" if isinstance(exc, KeyError) else str(message)}
    except Exception:
        logger.exception("Tool %s failed unexpectedly", name)
        return {"error": "internal error"}

    return _with_displays(result)
