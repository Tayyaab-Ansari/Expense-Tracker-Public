"""Tests for the monthly PDF report builder and the calendar view."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from reportlab.lib import colors
from reportlab.platypus import Table
from sqlalchemy.orm import Session

from app.llm.dispatcher import dispatch
from app.models import User
from app.reports import calendar_view, pdf_builder
from app.services import budgets, debts, goals
from app.services import transactions as tx
from app.utils.dates import parse_month

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


def test_report_includes_the_calendar(session: Session, filled: User) -> None:
    section = pdf_builder._calendar_section(session, filled.id, YEAR, MONTH)

    assert section
    assert section[0].getPlainText() == "Daily spending"
    assert isinstance(section[1], Table)
    assert section[1]._cellvalues[0] == calendar_view.WEEKDAYS
    assert section[-1].getPlainText().startswith("Highest day:")


def test_calendar_section_is_omitted_without_expenses(session: Session, user: User) -> None:
    tx.add_transactions(
        session,
        user.id,
        [{"type": "income", "amount": "5000", "category": "salary", "date": "2026-10-02"}],
    )

    assert pdf_builder._calendar_section(session, user.id, YEAR, MONTH) == []


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0", ""),
        ("450", "450"),
        ("999.49", "999"),
        ("1000", "1k"),
        ("1200", "1.2k"),
        ("9900", "9.9k"),
        ("15000", "15k"),
        ("150000", "150k"),
    ],
)
def test_short_amount(amount: str, expected: str) -> None:
    assert calendar_view.short_amount(Decimal(amount)) == expected


def test_calendar_shading_levels_and_white_text() -> None:
    totals = {
        date(2026, 10, 1): Decimal("120.00"),
        date(2026, 10, 5): Decimal("4000.00"),
        date(2026, 10, 14): Decimal("8450.00"),
    }

    table = calendar_view.build_calendar(totals, 2026, 10)
    backgrounds = {
        command[1]: command[3] for command in table._bkgrndcmds if command[0] == "BACKGROUND"
    }

    # 120/8450 is light, 4000/8450 is medium, the busiest day is the dark accent
    assert backgrounds[(3, 1)] == calendar_view.LEVEL_COLORS[1]
    assert backgrounds[(0, 2)] == calendar_view.LEVEL_COLORS[2]
    assert backgrounds[(2, 3)] == calendar_view.LEVEL_COLORS[3]

    dark_cell = table._cellvalues[3][2]
    assert dark_cell.style.textColor == colors.white
    assert "8.4k" in dark_cell.text


def test_calendar_has_one_row_per_week_and_blank_padding() -> None:
    table = calendar_view.build_calendar({}, 2026, 10)

    weeks = table._cellvalues[1:]
    assert len(weeks) == 5
    # October 2026 starts on a Thursday, so Mon-Wed of the first week are blank
    assert weeks[0][:3] == ["", "", ""]
    assert weeks[0][3].getPlainText().strip() == "1"
    assert not [
        command for command in table._bkgrndcmds if command[0] == "BACKGROUND" and command[1][1] > 0
    ]


def test_highest_day_text() -> None:
    totals = {date(2026, 10, 14): Decimal("8450.00"), date(2026, 10, 2): Decimal("100.00")}

    assert calendar_view.highest_day_text(totals) == "Highest day: 14 Oct · Rs 8,450"
    assert calendar_view.highest_day_text({}) is None


def test_generate_monthly_report_tool(session: Session, filled: User, reports_dir: Path) -> None:
    result = dispatch(session, filled.id, "generate_monthly_report", {"month": "october"})

    assert result["status"] == "report ready"
    document = result["document"]
    assert document["filename"] == "Expenses_Oct_2026.pdf"
    assert Path(document["path"]) == reports_dir / "Expenses_Oct_2026.pdf"
    assert Path(document["path"]).read_bytes().startswith(b"%PDF")
    assert document["caption"] == (
        "📊 October 2026 · Spent Rs 3,450 · Income Rs 80,000 · Net Rs 76,550"
    )
    assert json.dumps(result, ensure_ascii=False)


def test_generate_monthly_report_defaults_to_this_month(
    session: Session, user: User, reports_dir: Path
) -> None:
    year, month = parse_month(None)

    document = dispatch(session, user.id, "generate_monthly_report", {})["document"]

    assert document["filename"] == f"Expenses_{date(year, month, 1):%b}_{year}.pdf"


def test_generate_monthly_report_rejects_a_bad_month(session: Session, user: User) -> None:
    assert "error" in dispatch(session, user.id, "generate_monthly_report", {"month": "nope"})


def test_agent_collects_the_report_document(session: Session, user: User) -> None:
    result = dispatch(session, user.id, "generate_monthly_report", {"month": "october"})

    assert isinstance(result.get("document"), dict)
    assert set(result["document"]) == {"path", "filename", "caption"}


def test_legend_labels_every_level() -> None:
    legend = calendar_view.build_legend()

    assert [cell.getPlainText() for cell in legend._cellvalues[0]] == calendar_view.LEVEL_LABELS
