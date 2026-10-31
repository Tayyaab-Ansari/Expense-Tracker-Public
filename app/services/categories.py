"""Category service: list, create and resolve expense/income categories."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category

FALLBACK_NAMES = {"expense": "Other", "income": "Other Income"}


def list_categories(session: Session, user_id: int, kind: str | None = None) -> list[Category]:
    """List a user's categories, optionally restricted to one kind."""
    query = select(Category).where(Category.user_id == user_id)
    if kind is not None:
        query = query.where(Category.kind == kind)
    return list(session.scalars(query.order_by(Category.name)))


def _get_or_create(session: Session, user_id: int, name: str, kind: str) -> Category:
    """Return the named category for a kind, creating it when missing."""
    category = session.scalar(
        select(Category).where(
            Category.user_id == user_id,
            Category.kind == kind,
            func.lower(Category.name) == name.lower(),
        )
    )
    if category is None:
        category = Category(user_id=user_id, name=name, kind=kind)
        session.add(category)
        session.flush()
    return category


def resolve_category(session: Session, user_id: int, name: str | None, kind: str) -> Category:
    """Resolve a free-text category name for a kind, falling back to the Other category."""
    if kind not in FALLBACK_NAMES:
        raise ValueError(f"Unknown category kind: {kind!r}, expected expense or income")

    if name:
        wanted = name.strip().lower()
        categories = list_categories(session, user_id, kind)
        for category in categories:
            if category.name.lower() == wanted:
                return category
        for category in categories:
            if category.name.lower().startswith(wanted):
                return category

    return _get_or_create(session, user_id, FALLBACK_NAMES[kind], kind)


def add_category(session: Session, user_id: int, name: str, kind: str) -> dict:
    """Create a category, returning the existing one when it is already there."""
    if kind not in FALLBACK_NAMES:
        raise ValueError(f"Unknown category kind: {kind!r}, expected expense or income")
    clean = name.strip()
    if not clean:
        raise ValueError("Category name must not be empty")

    category = _get_or_create(session, user_id, clean.title(), kind)
    return {"id": category.id, "name": category.name, "kind": category.kind}
