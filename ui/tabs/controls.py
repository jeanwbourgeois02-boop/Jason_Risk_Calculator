"""Shared source-dropdown / as-of-date-picker controls for tabs that query
`marks_official` with an optional raw-source override.

Factored out of `ui/tabs/cash_ladder.py` once `ui/tabs/pnl.py` needed the identical
pair (both tabs read `marks_official` by default, or one explicit reconciliation-only
`marks.source` such as `BNP_BVAL` -- see CLAUDE.md "Official marks": BNP_BVAL is never
official). Each caller supplies its own component ids so the two tabs' callbacks never
collide (Dash requires globally-unique component ids across the whole layout).
"""
from __future__ import annotations

from typing import Optional

from dash import dcc, html

SOURCE_OFFICIAL = "OFFICIAL"
SOURCE_OPTIONS = [
    {"label": "Workbook rates", "value": "WORKBOOK_REFERENCE"},
    {"label": "Official", "value": SOURCE_OFFICIAL},
    {"label": "BNP_BVAL", "value": "BNP_BVAL"},
]


def source_value_to_param(value: Optional[str]) -> Optional[str]:
    """Map the dropdown's sentinel 'OFFICIAL' (or an unset value) to source=None, the
    engine convention for "use marks_official". Any other value (e.g. 'BNP_BVAL')
    passes through unchanged."""
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
                value="WORKBOOK_REFERENCE",
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
