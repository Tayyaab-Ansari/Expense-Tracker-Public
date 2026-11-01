"""Calendar-style monthly spending view for reports."""

import calendar
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Table, TableStyle

from app.utils.money import format_rs

ACCENT = colors.HexColor("#1F7A5C")
GRID = colors.HexColor("#D9D9D9")
MUTED = colors.HexColor("#666666")

# white, light, medium, dark accent
LEVEL_COLORS = [
    colors.white,
    colors.HexColor("#D6EAE2"),
    colors.HexColor("#8FC9B6"),
    ACCENT,
]
LEVEL_LABELS = ["None", "Low", "Medium", "High"]
DARK_LEVEL = 3
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CELL_WIDTH = 25 * mm
CELL_HEIGHT = 13 * mm

DAY_STYLE = ParagraphStyle(
    "CalendarDay", fontName="Helvetica", fontSize=8, leading=9, textColor=colors.black
)
DAY_STYLE_DARK = ParagraphStyle("CalendarDayDark", parent=DAY_STYLE, textColor=colors.white)
LEGEND_STYLE = ParagraphStyle(
    "CalendarLegend", fontName="Helvetica", fontSize=7, leading=8, textColor=MUTED
)
CAPTION_STYLE = ParagraphStyle(
    "CalendarCaption", fontName="Helvetica", fontSize=9, leading=11, textColor=MUTED
)


def short_amount(amount: Decimal) -> str:
    """Compact amount for a calendar cell: 450, 1.2k, 15k; blank when zero."""
    if not amount:
        return ""
    value = abs(amount)
    if value < 1000:
        return f"{value:.0f}"
    thousands = value / 1000
    if thousands < 10:
        return f"{thousands:.1f}k".replace(".0k", "k")
    return f"{thousands:.0f}k"


def _level(amount: Decimal, highest: Decimal) -> int:
    """Shading level of a day relative to the busiest day of the month."""
    if not amount or not highest:
        return 0
    share = amount / highest
    if share <= Decimal("0.33"):
        return 1
    if share <= Decimal("0.66"):
        return 2
    return DARK_LEVEL


def _cell(day: int, amount: Decimal, level: int) -> Paragraph:
    """Day number above its total, coloured for the shading level."""
    style = DAY_STYLE_DARK if level == DARK_LEVEL else DAY_STYLE
    text = short_amount(amount)
    return Paragraph(f'<font size="6">{day}</font><br/>{text}', style)


def build_calendar(daily_totals: dict[date, Decimal], year: int, month: int) -> Table:
    """Month grid of daily spending, shaded by how heavy each day was."""
    highest = max(daily_totals.values(), default=Decimal("0"))
    rows: list[list] = [WEEKDAYS]
    shading: list[tuple[int, int, int]] = []

    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week_index, week in enumerate(weeks, start=1):
        row = []
        for day_index, day in enumerate(week):
            if day == 0:
                row.append("")
                continue
            amount = daily_totals.get(date(year, month, day), Decimal("0"))
            level = _level(amount, highest)
            row.append(_cell(day, amount, level))
            if level:
                shading.append((day_index, week_index, level))
        rows.append(row)

    table = Table(
        rows,
        colWidths=[CELL_WIDTH] * 7,
        rowHeights=[None] + [CELL_HEIGHT] * len(weeks),
        hAlign="LEFT",
    )
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 1), (-1, -1), 3),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
    ]
    style.extend(
        ("BACKGROUND", (column, row), (column, row), LEVEL_COLORS[level])
        for column, row, level in shading
    )
    table.setStyle(TableStyle(style))
    return table


def build_legend() -> Table:
    """Single row explaining the four shading levels."""
    cells = [Paragraph(label, LEGEND_STYLE) for label in LEVEL_LABELS]
    table = Table([cells], colWidths=[18 * mm] * len(cells), rowHeights=[5 * mm], hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]
    style.extend(
        ("BACKGROUND", (index, 0), (index, 0), colour)
        for index, colour in enumerate(LEVEL_COLORS)
    )
    table.setStyle(TableStyle(style))
    return table


def highest_day_text(daily_totals: dict[date, Decimal]) -> str | None:
    """Line naming the busiest day of the month, or None when there is none."""
    if not daily_totals:
        return None
    day, amount = max(daily_totals.items(), key=lambda item: item[1])
    return f"Highest day: {day.day} {day:%b} · {format_rs(amount)}"


def build_calendar_flowables(
    daily_totals: dict[date, Decimal], year: int, month: int
) -> list:
    """Calendar table with its legend and the highest-day line."""
    flowables = [build_calendar(daily_totals, year, month), build_legend()]
    caption = highest_day_text(daily_totals)
    if caption:
        flowables.append(Paragraph(caption, CAPTION_STYLE))
    return flowables
