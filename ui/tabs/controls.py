"""Shared source-dropdown / as-of-date-picker controls for tabs that query
`marks_official`.

Factored out of `ui/tabs/cash_ladder.py` once a second tab needed the identical pair. Each
caller supplies its own component ids so the tabs' callbacks never collide (Dash requires
globally-unique component ids across the whole layout).

The source dropdown offers "Official" only (the `marks_official` view, CLAUDE.md "Official
marks"). The "Workbook rates" (WORKBOOK_REFERENCE) and BNP_BVAL options were removed
2026-09-24: both sources are retired (hard rule 1), nothing writes those marks any more and
`schema.purge_retired_sources` deletes what an old database still holds.
"""
from __future__ import annotations

from typing import Optional

from dash import dcc, html

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
