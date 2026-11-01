"""ReportLab PDF builder for expense reports."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from app.services.analytics import compare_months, spending_summary, top_expenses
from app.services.budgets import budget_status
from app.services.debts import debt_summary
from app.services.goals import goals_status
from app.utils.dates import human_date, month_bounds, today
from app.utils.money import format_rs

ACCENT = colors.HexColor("#1F7A5C")
GRID = colors.HexColor("#D9D9D9")
MUTED = colors.HexColor("#666666")
MARGIN = 15 * mm
REPORTS_DIR = Path("data/reports")
TOP_EXPENSES = 10
ZERO = Decimal("0.00")

_styles = getSampleStyleSheet()
TITLE_STYLE = ParagraphStyle(
    "ReportTitle",
    parent=_styles["Title"],
    fontName="Helvetica-Bold",
    fontSize=18,
    textColor=ACCENT,
    spaceAfter=4,
)
SUBTITLE_STYLE = ParagraphStyle(
    "ReportSubtitle",
    parent=_styles["Normal"],
    fontName="Helvetica",
    fontSize=10,
    textColor=MUTED,
    alignment=TA_CENTER,
    spaceAfter=12,
)
HEADING_STYLE = ParagraphStyle(
    "ReportHeading",
    parent=_styles["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=12,
    textColor=ACCENT,
    spaceBefore=14,
    spaceAfter=6,
)
BODY_STYLE = ParagraphStyle(
    "ReportBody", parent=_styles["Normal"], fontName="Helvetica", fontSize=10
)


def _table(rows: list[list[str]], widths: list[float], numeric_from: int = 1) -> Table:
    """Table with the report's header, grid and alignment."""
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("ALIGN", (numeric_from, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _previous_month(year: int, month: int) -> tuple[int, int]:
    """Year and month before the given one."""
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _pct_text(value: Decimal | None) -> str:
    """Percentage with a sign, or a dash when it cannot be computed."""
    if value is None:
        return "—"
    return f"{value:+.1f}%"


def _has_transactions(session: Session, user_id: int, year: int, month: int) -> bool:
    """Whether the month holds any expense or income."""
    start, end = month_bounds(year, month)
    expenses = spending_summary(session, user_id, start, end)["count"]
    incomes = spending_summary(session, user_id, start, end, type="income")["count"]
    return bool(expenses or incomes)


def _summary_section(session: Session, user_id: int, year: int, month: int) -> list:
    """Income, expenses, net and the change against last month."""
    start, end = month_bounds(year, month)
    expense = spending_summary(session, user_id, start, end)["total"]
    income = spending_summary(session, user_id, start, end, type="income")["total"]
    comparison = compare_months(session, user_id, _previous_month(year, month), (year, month))

    rows = [
        ["Summary", "Amount"],
        ["Income", format_rs(income)],
        ["Expenses", format_rs(expense)],
        ["Net", format_rs(income - expense)],
        ["Expenses vs last month", _pct_text(comparison["expense_change_pct"])],
    ]
    return [_table(rows, [90 * mm, 50 * mm])]


def _category_section(session: Session, user_id: int, year: int, month: int) -> list:
    """Expenses per category with budget usage where a budget exists."""
    start, end = month_bounds(year, month)
    summary = spending_summary(session, user_id, start, end)
    if not summary["by_category"]:
        return []

    budgets = {
        item["category"]: item for item in budget_status(session, user_id, year, month)["items"]
    }
    rows = [["Category", "Amount", "% of total", "Budget", "Used %"]]
    for row in summary["by_category"]:
        budget = budgets.get(row["category"])
        rows.append(
            [
                row["category"],
                format_rs(row["total"]),
                f"{row['pct']:.1f}%",
                format_rs(budget["budget"]) if budget else "",
                f"{budget['pct']:.0f}%" if budget else "",
            ]
        )

    return [
        Paragraph("Expenses by category", HEADING_STYLE),
        _table(rows, [55 * mm, 30 * mm, 25 * mm, 30 * mm, 20 * mm]),
    ]


def _account_section(session: Session, user_id: int, year: int, month: int) -> list:
    """Money spent and received per account."""
    start, end = month_bounds(year, month)
    spent = {
        row["account"]: row["total"]
        for row in spending_summary(session, user_id, start, end)["by_account"]
    }
    received = {
        row["account"]: row["total"]
        for row in spending_summary(session, user_id, start, end, type="income")["by_account"]
    }
    if not spent and not received:
        return []

    rows = [["Account", "Spent", "Received"]]
    for name in sorted(spent.keys() | received.keys()):
        rows.append(
            [name, format_rs(spent.get(name, ZERO)), format_rs(received.get(name, ZERO))]
        )
    return [Paragraph("By account", HEADING_STYLE), _table(rows, [60 * mm, 40 * mm, 40 * mm])]


def _top_expenses_section(session: Session, user_id: int, year: int, month: int) -> list:
    """The largest expenses of the month."""
    start, end = month_bounds(year, month)
    items = top_expenses(session, user_id, start, end, n=TOP_EXPENSES)
    if not items:
        return []

    rows = [["Date", "Category", "Description", "Account", "Amount"]]
    for item in items:
        rows.append(
            [
                item["date"].strftime("%d %b"),
                item["category"] or "",
                item["description"] or "",
                item["account"] or "",
                format_rs(item["amount"]),
            ]
        )
    return [
        Paragraph(f"Top {len(items)} expenses", HEADING_STYLE),
        _table(rows, [20 * mm, 35 * mm, 55 * mm, 30 * mm, 30 * mm], numeric_from=4),
    ]


def _debts_section(session: Session, user_id: int) -> list:
    """Outstanding debts, when there are any."""
    people = [row for row in debt_summary(session, user_id)["people"] if row["net"] != 0]
    if not people:
        return []

    rows = [["Person", "Amount", "Status"]]
    for row in people:
        rows.append([row["person"], format_rs(abs(row["net"])), row["status"]])
    return [
        Paragraph("Debts outstanding", HEADING_STYLE),
        _table(rows, [60 * mm, 40 * mm, 40 * mm]),
    ]


def _goals_section(session: Session, user_id: int) -> list:
    """Savings goals, when there are any."""
    goals = goals_status(session, user_id)
    if not goals:
        return []

    rows = [["Goal", "Saved", "Target", "Progress"]]
    for goal in goals:
        rows.append(
            [
                goal["name"],
                format_rs(goal["saved"]),
                format_rs(goal["target"]),
                f"{goal['pct']:.0f}%",
            ]
        )
    return [
        Paragraph("Savings goals", HEADING_STYLE),
        _table(rows, [50 * mm, 35 * mm, 35 * mm, 30 * mm]),
    ]


def build_monthly_report(
    session: Session, user_id: int, year: int, month: int, owner_name: str
) -> Path:
    """Build the monthly PDF report and return its path."""
    start, _ = month_bounds(year, month)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"Expenses_{start:%b}_{year}.pdf"

    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"Expense Report {start:%B %Y}",
        author=owner_name,
    )

    story = [
        Paragraph(f"Expense Report — {start:%B %Y}", TITLE_STYLE),
        Paragraph(f"{owner_name} · generated {human_date(today())}", SUBTITLE_STYLE),
    ]

    if _has_transactions(session, user_id, year, month):
        story.extend(
            [
                *_summary_section(session, user_id, year, month),
                # calendar inserted in P31
                *_category_section(session, user_id, year, month),
                *_account_section(session, user_id, year, month),
                *_top_expenses_section(session, user_id, year, month),
                *_debts_section(session, user_id),
                *_goals_section(session, user_id),
            ]
        )
    else:
        story.append(Spacer(1, 10 * mm))
        story.append(Paragraph("No transactions this month", BODY_STYLE))

    document.build(story)
    return path
