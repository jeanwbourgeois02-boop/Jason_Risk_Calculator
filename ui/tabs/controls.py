"""Shared source-dropdown / as-of-date-picker controls for tabs that query
`marks_official`, and the screens' date helpers (`today_ny`, `calendar_today_ny`,
`heading_date_text`).

Factored out of the Ladder tab (`ui/tabs/cash_ladder.py`, deleted 2026-09-28 with the FX &
cash screen) once a second tab needed the identical pair; the date helpers came here from the
same module on that day. Each caller supplies its own component ids so the tabs' callbacks
never collide (Dash requires globally-unique component ids across the whole layout).

The source dropdown offers "Official" only (the `marks_official` view, CLAUDE.md "Official
marks"). The "Workbook rates" (WORKBOOK_REFERENCE) and BNP_BVAL options were removed
2026-09-24: both sources are retired (hard rule 1), nothing writes those marks any more and
`schema.purge_retired_sources` deletes what an old database still holds.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional
from zoneinfo import ZoneInfo

from dash import dcc, html

from data.bloomberg.live import ROLLOVER_HOUR_NY, book_today   # noqa: F401  (the day boundary; the constant is re-exported)

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")


def today_ny(now: Optional[dt.datetime] = None) -> str:
    """The book's "today" (ISO): the as-of every screen and the header default to.

    It is `data.bloomberg.live.book_today`, the app's ONE day boundary, as a string: the
    New York date until 17:00 New York and the NEXT date from then on (user, 2026-09-22:
    "I want to clarify the time today, so that all daily pnl is calculated from the NY 3pm
    the day before. I am based in HK, so basically all date rollover at hkt 5am", after
    "no only roll to new day after new york 5pm"; HKT 05:00 is 17:00 New York). The
    screens, the marks a pull stamps, the backfill's "past" and the ledger's freeze date
    all take their day from that one function, so at 18:00 New York on the 22nd the top
    bar values the 23rd, a pull writes marks dated the 23rd, and the 22nd is a past day
    whose 17:00 close the same press fetches: Daily is live against yesterday's 17:00
    close, never 0 for want of a date the screens and the marks agreed on. Nothing here
    decides the hour; `calendar_today_ny` below is the one date on the app that does not
    roll. `now` is for tests: a tz-aware datetime in any zone."""
    return book_today(now).isoformat()


def calendar_today_ny() -> str:
    """Today's calendar date in New York, no roll: what a trade dealt at 18:00 New York on
    the 22nd is dated (the 22nd)."""
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def heading_date_text(iso: Optional[str]) -> str:
    """'2026-09-15' -> 'Monday 15 September 2026' (a tab's title row); an unparseable or
    missing date falls back to a plain placeholder rather than raising, since this also runs
    before any date is picked."""
    if not iso:
        return "As of - no date selected"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return f"As of {iso}"
    return f"{_WEEKDAY_NAMES[d.weekday()]} {d.day} {_MONTH_NAMES[d.month - 1]} {d.year}"


SOURCE_OFFICIAL = "OFFICIAL"
SOURCE_OPTIONS = [
    {"label": "Official", "value": SOURCE_OFFICIAL},
]


def source_value_to_param(value: Optional[str]) -> Optional[str]:
    """Map the dropdown's sentinel 'OFFICIAL' (or an unset value) to source=None, the
    engine convention for "use marks_official". Any other value passes through unchanged."""
    if value in (None, SOURCE_OFFICIAL):
        return None
    return value


def build_source_dropdown(dropdown_id: str, label: str = "Source") -> html.Div:
    return html.Div(
        [
            html.Label(label),
            dcc.Dropdown(
                id=dropdown_id,
                options=SOURCE_OPTIONS,
                value=SOURCE_OFFICIAL,
                clearable=False,
            ),
        ],
        style={"width": "200px", "display": "inline-block", "marginRight": "20px"},
    )


def build_date_picker(picker_id: str, default_date: Optional[str] = None) -> html.Div:
    # Inline "AS OF" label + picker styled in ui/assets/style.css (.date-picker and the
    # react-dates overrides) to match the site's font, radius and button height
    # (user decision 2026-09-15: the stock picker's own font and stacked label looked
    # foreign on the title row).
    return html.Div(
        [
            html.Label("As of"),
            dcc.DatePickerSingle(id=picker_id, date=default_date, display_format="D MMM YYYY",
                                 first_day_of_week=1, number_of_months_shown=1),
        ],
        className="date-picker",
    )
