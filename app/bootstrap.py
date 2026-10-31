"""Idempotent seeding of default data and user lookup helpers."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, Category, User

logger = logging.getLogger(__name__)

DEFAULT_ACCOUNTS = ["Cash", "JazzCash", "Easypaisa", "Bank"]

DEFAULT_EXPENSE_CATEGORIES = [
    "Food",
    "Groceries",
    "Transport",
    "Fuel",
    "Bills",
    "Mobile & Internet",
    "Rent",
    "Shopping",
    "Health",
    "Education",
    "Entertainment",
    "Family",
    "Personal Care",
    "Gifts & Charity",
    "Travel",
    "Other",
]

DEFAULT_INCOME_CATEGORIES = [
    "Salary",
    "Freelance",
    "Family",
    "Gift",
    "Refund",
    "Other Income",
]


def get_or_create_user(session: Session, phone: str, name: str | None = None) -> User:
    """Return the user for a phone number, creating it with defaults on first sight."""
    user = session.scalar(select(User).where(User.phone == phone))
    if user is not None:
        return user

    user = User(phone=phone, name=name or phone)
    session.add(user)
    session.flush()

    accounts = [
        Account(user_id=user.id, name=account_name, is_default=account_name == DEFAULT_ACCOUNTS[0])
        for account_name in DEFAULT_ACCOUNTS
    ]
    session.add_all(accounts)
    session.add_all(
        Category(user_id=user.id, name=category_name, kind="expense", is_default=True)
        for category_name in DEFAULT_EXPENSE_CATEGORIES
    )
    session.add_all(
        Category(user_id=user.id, name=category_name, kind="income", is_default=True)
        for category_name in DEFAULT_INCOME_CATEGORIES
    )
    session.flush()

    user.default_account_id = accounts[0].id
    session.flush()
    logger.info("Created user %s with default accounts and categories", user.id)
    return user


def seed(session: Session) -> list[User]:
    """Create a user with defaults for every allowed number; safe to run repeatedly."""
    phones = sorted(settings.allowed_numbers)
    return [
        get_or_create_user(session, phone, settings.OWNER_NAME if index == 0 else None)
        for index, phone in enumerate(phones)
    ]


if __name__ == "__main__":
    from app.db import init_db, session_scope

    init_db()
    with session_scope() as seed_session:
        users = seed(seed_session)
        for seeded in users:
            print(f"{seeded.phone}: {seeded.name} (user {seeded.id})")
        print(f"Seeded {len(users)} user(s).")
