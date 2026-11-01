"""Tests for the monthly PDF report builder."""

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.reports import pdf_builder
from app.services import budgets, debts, goals
from app.services import transactions as tx

YEAR, MONTH = 2026, 10
MIN_SIZE = 1024


@pytest.fixture(autouse=True)
def reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write reports into a temporary directory."""
    directory = tmp_path / "reports"
    monkeypatch.setattr(pdf_builder, "REPORTS_DIR", directory)
    return directory


@pytest.fixture
def filled(session: Session, user: User) -> User:
    """A month with expenses, income, a budget, a debt and a goal."""
    tx.add_transactions(
        session,
        user.id,
        [
            {"amount": "150", "category": "food", "description": "chai", "date": "2026-10-05"},
            {"amount": "2500", "category": "groceries", "account": "bank", "date": "2026-10-07"},
            {"amount": "800", "category": "transport", "account": "jazz", "date": "2026-10-09"},
            {"type": "income", "amount": "80000", "category": "salary", "account": "bank", "date": "2026-10-01"},
            {"amount": "1000", "category": "food", "date": "2026-09-15"},
        ],
    )
    budgets.set_budget(session, user.id, "food", "5000")
    debts.record_debt(session, user.id, "ali", "lent", "2000", date="2026-10-03")
    goals.create_goal(session, user.id, "Laptop", "150000", deadline="2027-06-30")
    goals.add_to_goal(session, user.id, "laptop", "45000", date="2026-10-04")
    return user


def test_report_is_created(session: Session, filled: User, reports_dir: Path) -> None:
    path = pdf_builder.build_monthly_report(session, filled.id, YEAR, MONTH, "Tayyaab")

    assert path == reports_dir / "Expenses_Oct_2026.pdf"
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")
    assert path.stat().st_size > MIN_SIZE


def test_report_creates_the_folder(session: Session, filled: User, reports_dir: Path) -> None:
    assert not reports_dir.exists()

    pdf_builder.build_monthly_report(session, filled.id, YEAR, MONTH, "Tayyaab")

    assert reports_dir.is_dir()


def test_empty_month_still_builds(session: Session, user: User, reports_dir: Path) -> None:
    path = pdf_builder.build_monthly_report(session, user.id, YEAR, MONTH, "Tayyaab")

    assert path.read_bytes().startswith(b"%PDF")
    assert path.stat().st_size > MIN_SIZE


def test_income_only_month_is_not_treated_as_empty(session: Session, user: User) -> None:
    tx.add_transactions(
        session,
        user.id,
        [{"type": "income", "amount": "5000", "category": "salary", "date": "2026-10-02"}],
    )

    assert pdf_builder._has_transactions(session, user.id, YEAR, MONTH)


def test_month_without_debts_or_goals_builds(session: Session, user: User) -> None:
    tx.add_transactions(
        session, user.id, [{"amount": "150", "category": "food", "date": "2026-10-05"}]
    )

    path = pdf_builder.build_monthly_report(session, user.id, YEAR, MONTH, "Tayyaab")

    assert path.read_bytes().startswith(b"%PDF")
    assert pdf_builder._debts_section(session, user.id) == []
    assert pdf_builder._goals_section(session, user.id) == []


def test_report_is_rebuilt_in_place(session: Session, filled: User) -> None:
    first = pdf_builder.build_monthly_report(session, filled.id, YEAR, MONTH, "Tayyaab")
    size = first.stat().st_size

    second = pdf_builder.build_monthly_report(session, filled.id, YEAR, MONTH, "Tayyaab")

    assert second == first
    assert second.stat().st_size >= size // 2


def test_filename_uses_the_requested_month(session: Session, filled: User) -> None:
    path = pdf_builder.build_monthly_report(session, filled.id, 2026, 1, "Tayyaab")

    assert path.name == "Expenses_Jan_2026.pdf"
