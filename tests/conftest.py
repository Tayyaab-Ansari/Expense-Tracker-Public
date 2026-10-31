"""Pytest fixtures providing an in-memory SQLite session and seeded user."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.bootstrap import get_or_create_user
from app.models import Base, User


@pytest.fixture
def session() -> Iterator[Session]:
    """Fresh in-memory database with all tables created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db
    engine.dispose()


@pytest.fixture
def user(session: Session) -> User:
    """Seeded test user with default accounts and categories."""
    return get_or_create_user(session, "923000000000", "Test")
