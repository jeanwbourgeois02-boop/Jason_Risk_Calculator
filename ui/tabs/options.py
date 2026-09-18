"""Options view inside the Blotter tab (options_calc merge Phase 8, 2026-09-17):
a grouped, collapsible MARS-style risk grid -- Portfolio Totals -> asset class (FX;
Equity/Commodity present-but-empty until a live feed exists for them, per
`engine/options/__init__.py`'s own note that no data-ingest parser exists yet for
EQ_OPTION/CMDTY_OPTION) -> structure/package (one row per `trades.package_id`) -> leg.

**Read-only, never re-prices.** Unlike `engine/options/structures.py::combine_package`
(which calls `price_and_store` and WRITES marks), this module only ever READS the
marks `engine/options/store.py` already wrote (`PREMIUM`/`DELTA`/`GAMMA`/`THETA`/
`VEGA`/`RHO`, `source='QL_OPTIONS_PRICER'`, official per
`data/ingest/schema.py::OFFICIAL_MARK_SOURCE`), straight off `marks_official` --
matching every other Blotter sub-tab (`ui/tabs/rates.py`'s docstring: "every priced
column here reads marks_official, never marks directly") and the "ui/ ... never
recomputes P&L or delta itself" rule in CLAUDE.md. `trades_official` is read for the
same double-counting reason `ui/tabs/rates.py` reads it (BNP vs blotter trade-id
schemes, `docs/open-questions.md` item 55).

**Two different currency conversions, by design, not an inconsistency:**
  - MktVal = PREMIUM (base-notional fraction) x quantity (base ccy) x BASE ccy -> USD
    spot -- literal task mapping, independent of the Greeks' own conversion below.
  - Delta/Theta/Gamma/Vega/Rho = the vendored pricers' native QUOTE-ccy-denominated
    sensitivity x (quantity x multiplier) x QUOTE ccy -> USD spot, exactly
    `engine/options/portfolio.py`'s documented formula (that module's docstring
    explains why quote ccy, not base ccy, is correct for a per-unit-move
    sensitivity). This module builds `store.PricingOutcome`-shaped objects purely
    from marks already on file (never re-pricing) and feeds
    `engine.options.portfolio.build_positions` for that conversion, per the task's
    "use it rather than re-summing yourself" instruction.

**Aggregation rule.** Every group level (PACKAGE / ASSET_CLASS / TOTAL) is a plain
sum-skip-missing over its own legs for the eight numeric columns (Position, Notional,
MktVal, Delta, Theta, Gamma, Vega, Rho) -- `_agg`, applied uniformly bottom-up, so
TOTAL always equals the sum of its asset-class rows, which always equals the sum of
their package rows, which always equals the sum of their legs. The five
leg-identity columns (MktPx, Expiry, Underlying, Strike, UndFwdPx) are not
aggregatable across different instruments; a group shows them only when it has
exactly one contributing leg (`_single`) -- which is also how a single-leg package
renders "flat": its PACKAGE row IS that one leg's row, with no separate LEG row
beneath it (task instruction: "a single-leg option is its own one-row package").
A multi-leg package's PACKAGE row is a pure Greek/MktVal summary (those five
identity columns blank) with its LEG rows nested beneath, collapsed by default.

**Collapse mechanism.** No native tree in `dash_table.DataTable` (rates.py /
blotter.py precedent: no dropdown-filter widget exists either). A `dcc.Store` of
collapsed package_ids plus a server callback filters LEG rows whose `parent_key` is
in that set, re-running `option_rows` against the current as-of date on every
toggle -- mirroring how the Blotter's own filter-dropdown callbacks
(`ui/tabs/blotter.py::_register_filter_callback`) re-query on every selection
change rather than caching client-side. `date_picker_id` defaults to the Blotter
tab's own `"blotter-date"` id (a string literal, not an import of `ui.tabs.blotter`,
which would create a circular import since `blotter.py` imports this module).
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Iterable, List, Optional

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs.formatting import format_cell

TABLE_ID = "options-datatable"
COLLAPSED_STORE_ID = "options-collapsed-packages"
DEFAULT_DATE_PICKER_ID = "blotter-date"

# Column order per task instruction, exactly: Position, Notional, MktVal, MktPx,
# Delta, Theta, Gamma, Vega, Expiry, Underlying, Strike, UndFwdPx, Rho. "label" is
# the row name/tree column, prepended -- not one of the 13 instructed columns.
DISPLAY_COLUMNS = [
    "label", "type", "position", "notional", "mktval", "mktpx", "delta", "theta", "gamma",
    "vega", "expiry", "underlying", "strike", "undfwdpx", "rho", "instrument",
]
# Carried in every row's data so filter_query / the collapse callback can key off
# them, but not shown -- `hidden_columns`, not omitted from `columns`, so filter_query
# can still reference them (Dash evaluates filter_query against defined columns).
HIDDEN_COLUMNS = ["level", "group_key", "parent_key", "leg_count"]
# "type" (2026-09-17, user requirement "say which option type it is"): payoff and
# call/put, e.g. "Vanilla Call", "Digital Put"; a multi-leg package says "2 legs".
# "instrument" is the blotter instrument id, so a row can be tied back to the trade file.
PAYOFF_WORDS = {"VANILLA": "Vanilla", "DIGITAL": "Digital", "AMERICAN": "American", "ASIAN": "Asian",
                "BARRIER_KI": "Knock-in", "BARRIER_KO": "Knock-out", "ONE_TOUCH": "One-touch",
                "NO_TOUCH": "No-touch"}
_LEG_WORDS = {2: "Two", 3: "Three", 4: "Four"}
ALL_COLUMNS = DISPLAY_COLUMNS + HIDDEN_COLUMNS

COLUMN_LABELS = {
    "label": "Structure", "type": "Type", "instrument": "Instrument", "position": "Position", "notional": "Notional",
    "mktval": "MktVal", "mktpx": "MktPx", "delta": "Delta", "theta": "Theta",
    "gamma": "Gamma", "vega": "Vega", "expiry": "Expiry", "underlying": "Underlying",
    "strike": "Strike", "undfwdpx": "UndFwdPx", "rho": "Rho",
}

NUMERIC_FIELDS = ("position", "notional", "mktval", "delta", "theta", "gamma", "vega", "rho")
PASSTHROUGH_FIELDS = ("mktpx", "expiry", "underlying", "strike", "undfwdpx", "type", "instrument")

# instruments.asset_class / trades.product values (engine/options/equity_commodity.py
# docstring: "New instruments.asset_class values introduced here: 'EQ_OPTION',
# 'CMDTY_OPTION'"). No data-ingest parser exists yet for either (that module's own
# docstring), so these groups render present-but-empty on this app today -- by
# design, not a bug, per the task's "rows must always render" instruction.
ASSET_CLASS_BY_PRODUCT = {"FX_OPTION": "FX", "EQ_OPTION": "Equity", "CMDTY_OPTION": "Commodity"}
ASSET_CLASS_ORDER = ("FX", "Equity", "Commodity")

_GREEK_MARK_TYPES = (("delta", "DELTA"), ("theta", "THETA"), ("gamma", "GAMMA"),
                     ("vega", "VEGA"), ("rho", "RHO"))


# --------------------------------------------------------------------------- reads

def _is_missing(value) -> bool:
    return value is None or value != value  # NaN != NaN


def _spot_to_usd(conn: sqlite3.Connection, as_of: str, ccy: str) -> Optional[float]:
    """USD per 1 unit of `ccy` from the official SPOT, mirroring
    `engine/options/portfolio.py::_quote_ccy_to_usd` (same rule, generalised to
    either leg of a pair: CLAUDE.md -- quote-ccy P&L converts to USD at spot, never
    the forward outright). None if neither `ccy+USD` nor `USD+ccy` has one."""
    if ccy == "USD":
        return 1.0
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, ccy + "USD"),
    ).fetchone()
    if row is not None:
        return row[0]
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, "USD" + ccy),
    ).fetchone()
    if row is not None and row[0]:
        return 1.0 / row[0]
    return None


def _instrument_options_columns(conn: sqlite3.Connection) -> set:
    """Columns actually present on this DB's `instrument_options` table (2026-09-17 dev-DB
    fix): `CREATE TABLE IF NOT EXISTS` in `data/ingest/schema.py` never adds a column to an
    already-existing table, so a DB created before the `payoff` column landed (this app's
    dev DB, `data/raw/risk.db`) still has the 4-column version and a bare `SELECT
    o.payoff` throws `OperationalError: no such column`, which was never caught -- the
    whole Options sub-tab returned an HTTP 500 and the tab looked like it "did not load"
    (no partial render, no message, just a dead callback). Queried live (not memoised):
    this is one cheap PRAGMA per render, and the alternative -- caching across a schema
    change -- risks silently hiding a real migration gap. Real fix belongs in
    `data/ingest/schema.py` (an `ALTER TABLE instrument_options ADD COLUMN ...` migration
    for pre-existing DBs); reported, not made here (outside this agent's owned files)."""
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(instrument_options)").fetchall()}
    except sqlite3.Error:
        return set()


def _official_marks(conn: sqlite3.Connection, as_of: str, instrument_id: str,
                     settle_date: str, mark_types: Iterable[str]) -> dict:
    mark_types = tuple(mark_types)
    placeholders = ",".join("?" * len(mark_types))
    rows = conn.execute(
        f"SELECT mark_type, value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? "
        f"AND settle_date = ? AND mark_type IN ({placeholders})",
        (as_of, instrument_id, settle_date, *mark_types),
    ).fetchall()
    return dict(rows)


def _leg_row(conn: sqlite3.Connection, as_of: str, rec: dict) -> dict:
    """One priced (or partially/un-priced -- 'rows must always render') leg, built
    straight from `trades_official`/`instruments`/`instrument_options`/`marks_official`,
    no re-pricing. See module docstring for the two conversion rules."""
    asset_class = ASSET_CLASS_BY_PRODUCT.get(rec["product"], "FX")
    quantity = rec["quantity"]
    multiplier = rec["multiplier"] or 1.0

    marks = _official_marks(conn, as_of, rec["instrument_id"], rec["expiry_date"],
                             ("PREMIUM",) + tuple(mt for _, mt in _GREEK_MARK_TYPES))
    premium = marks.get("PREMIUM")
    base_spot = _spot_to_usd(conn, as_of, rec["base_ccy"])
    quote_spot = _spot_to_usd(conn, as_of, rec["quote_ccy"])

    mktval = (premium * quantity * base_spot
              if premium is not None and base_spot is not None else None)

    greeks = {}
    for col, mark_type in _GREEK_MARK_TYPES:
        raw = marks.get(mark_type)
        greeks[col] = (raw * (quantity * multiplier) * quote_spot
                       if raw is not None and quote_spot is not None else None)

    if asset_class == "FX":
        underlying = rec["base_ccy"] + rec["quote_ccy"]
        fwd = _official_marks(conn, as_of, underlying, rec["expiry_date"], ("FWD_OUTRIGHT",))
        undfwdpx = fwd.get("FWD_OUTRIGHT")
    else:
        # Equity/commodity: bbg_ticker root (task mapping) -- no live trades to
        # exercise this branch today (module docstring); a forward outright has no
        # meaning for a single-underlying equity/commodity option.
        ticker = rec["bbg_ticker"] or ""
        underlying = ticker.split()[0] if ticker else ""
        undfwdpx = None

    strike = rec["strike"] if rec["strike"] else None  # 0 = not known (schema.py sentinel) -> blank
    payoff_word = PAYOFF_WORDS.get(rec.get("payoff") or "VANILLA", (rec.get("payoff") or "").title())
    cp = (rec.get("option_type") or "").title()
    type_text = f"{payoff_word} {cp}".strip()
    if strike is None and (rec.get("payoff") or "VANILLA") not in ("ONE_TOUCH", "NO_TOUCH"):
        type_text += " (no strike on file)"

    return {
        "trade_id": rec["trade_id"], "package_id": rec["package_id"], "asset_class": asset_class,
        "label": f"{underlying} - {payoff_word}" if underlying else payoff_word,
        "type": type_text, "instrument": rec["instrument_id"], "payoff_word": payoff_word,
        "position": quantity, "notional": abs(quantity), "mktval": mktval, "mktpx": premium,
        "delta": greeks["delta"], "theta": greeks["theta"], "gamma": greeks["gamma"],
        "vega": greeks["vega"], "rho": greeks["rho"],
        "expiry": rec["expiry_date"], "underlying": underlying, "strike": strike,
        "undfwdpx": undfwdpx,
    }


def _leg_rows(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    opt_cols = _instrument_options_columns(conn)
    strike_expr = "COALESCE(o.strike, 0)" if "strike" in opt_cols else "0"
    option_type_expr = "COALESCE(o.option_type, '')" if "option_type" in opt_cols else "''"
    payoff_expr = "COALESCE(o.payoff, 'VANILLA')" if "payoff" in opt_cols else "'VANILLA'"
    trades = pd.read_sql_query(
        "SELECT t.trade_id, t.package_id, t.instrument_id, t.quantity, t.product, "
        "i.base_ccy, i.quote_ccy, i.multiplier, i.expiry_date, i.bbg_ticker, "
        f"{strike_expr} AS strike, {option_type_expr} AS option_type, "
        f"{payoff_expr} AS payoff "
        "FROM trades_official t JOIN instruments i ON i.instrument_id = t.instrument_id "
        "LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id "
        "WHERE t.product IN ('FX_OPTION','EQ_OPTION','CMDTY_OPTION') "
        "ORDER BY t.package_id, t.trade_id",
        conn,
    )
    return [_leg_row(conn, as_of, rec) for rec in trades.to_dict("records")]


# --------------------------------------------------------------------------- aggregation

def _agg(values: Iterable[Optional[float]]) -> Optional[float]:
    """Sum, skipping missing legs; None (never 0) when every leg is missing or there
    are no legs at all -- the "rows must always render, missing stays missing" rule,
    extended to group sums."""
    vals = [v for v in values if not _is_missing(v)]
    return sum(vals) if vals else None


def _single(values: Iterable) -> Optional[object]:
    """The one value present when a group has exactly one contributing leg, else
    None -- how a single-leg package "is its own one-row package" (its identity
    columns pass straight through) while a multi-leg group leaves them blank
    (not meaningful to combine a strike/expiry/underlying across legs)."""
    vals = list(values)
    return vals[0] if len(vals) == 1 else None


def _row(level: str, group_key: str, parent_key: str, label: str, leg_count: int,
         numeric: dict, passthrough: dict) -> dict:
    row = {"level": level, "group_key": group_key, "parent_key": parent_key,
           "label": label, "leg_count": leg_count}
    row.update(numeric)
    row.update(passthrough)
    return row


def option_rows(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """TOTAL -> ASSET_CLASS (FX always; Equity/Commodity present-but-empty) ->
    PACKAGE (one per `trades.package_id`, flat when it has exactly one leg) -> LEG
    (only emitted for a package with >1 leg)."""
    legs = _leg_rows(conn, as_of)
    rows: List[dict] = []

    def _numeric(group_legs: List[dict]) -> dict:
        return {f: _agg(l[f] for l in group_legs) for f in NUMERIC_FIELDS}

    def _passthrough(group_legs: List[dict]) -> dict:
        return {f: _single(l[f] for l in group_legs) for f in PASSTHROUGH_FIELDS}

    rows.append(_row("TOTAL", "TOTAL", "", "Portfolio Totals", len(legs),
                      _numeric(legs), _passthrough(legs)))

    for cls in ASSET_CLASS_ORDER:
        cls_legs = [l for l in legs if l["asset_class"] == cls]
        rows.append(_row("ASSET_CLASS", cls, "TOTAL", cls, len(cls_legs),
                          _numeric(cls_legs), _passthrough(cls_legs)))

        seen_pkgs: List[str] = []
        for l in cls_legs:
            if l["package_id"] not in seen_pkgs:
                seen_pkgs.append(l["package_id"])
        for pkg in seen_pkgs:
            pkg_legs = [l for l in cls_legs if l["package_id"] == pkg]
            if len(pkg_legs) == 1:
                pkg_label = pkg_legs[0]["label"]
                pkg_pass = _passthrough(pkg_legs)
            else:
                # MARS-style structure name: "USDCHF - Two Leg" when every leg shares the
                # underlying, else the package id; the type column counts the legs.
                unders = {l["underlying"] for l in pkg_legs}
                n = len(pkg_legs)
                word = _LEG_WORDS.get(n, str(n))
                pkg_label = f"{unders.pop()} - {word} Leg" if len(unders) == 1 else pkg
                pkg_pass = _passthrough(pkg_legs)
                pkg_pass["type"] = f"{n} legs"
                pkg_pass["instrument"] = pkg
            rows.append(_row("PACKAGE", pkg, cls, pkg_label, len(pkg_legs),
                              _numeric(pkg_legs), pkg_pass))
            if len(pkg_legs) > 1:
                for l in pkg_legs:
                    leg_numeric = {f: l[f] for f in NUMERIC_FIELDS}
                    leg_pass = {f: l[f] for f in PASSTHROUGH_FIELDS}
                    rows.append(_row("LEG", l["trade_id"], pkg, l["payoff_word"], 1,
                                      leg_numeric, leg_pass))

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- formatting

def _fmt_usd_or_na(value) -> str:
    return "n/a" if _is_missing(value) else format_cell(value)


def _fmt_usd_or_blank(value) -> str:
    return "" if _is_missing(value) else format_cell(value)


def _fmt_price(value) -> str:
    return "n/a" if _is_missing(value) else f"{float(value):.6f}"


def _fmt_rate_blank(value) -> str:
    return "" if _is_missing(value) else f"{float(value):,.6f}"


def _fmt_rate_na(value) -> str:
    return "n/a" if _is_missing(value) else f"{float(value):,.6f}"


def _fmt_label(rec: dict, collapsed: set) -> str:
    label = rec["label"]
    if rec["level"] == "PACKAGE" and rec.get("leg_count") and rec["leg_count"] > 1:
        arrow = "▸" if rec["group_key"] in collapsed else "▾"  # collapsed / expanded
        return f"{arrow} {label}"
    return label


def default_collapsed_packages(df: pd.DataFrame) -> List[str]:
    """Packages with >1 leg start collapsed; a single-leg package has no toggle at
    all (task instruction: "single-leg packages shown flat")."""
    if df.empty:
        return []
    mask = (df["level"] == "PACKAGE") & (df["leg_count"] > 1)
    return sorted(df.loc[mask, "group_key"].tolist())


def format_rows(df: pd.DataFrame, collapsed: Optional[Iterable[str]] = None) -> tuple:
    """`(data_records, style_data_conditional)`, unit-testable without Dash -- same
    convention as `ui.tabs.rates.format_rows` / `ui.tabs.blotter._format_rows`. LEG
    rows whose `parent_key` is in `collapsed` are dropped from the output."""
    collapsed_set = set(collapsed or [])
    records = []
    for rec in df.to_dict("records"):
        if rec["level"] == "LEG" and rec["parent_key"] in collapsed_set:
            continue
        records.append({
            "level": rec["level"], "group_key": rec["group_key"], "parent_key": rec["parent_key"],
            "leg_count": rec["leg_count"],
            "label": _fmt_label(rec, collapsed_set),
            "type": rec.get("type") or "",
            "instrument": rec.get("instrument") or "",
            "position": _fmt_usd_or_blank(rec["position"]),
            "notional": _fmt_usd_or_blank(rec["notional"]),
            "mktval": _fmt_usd_or_na(rec["mktval"]),
            "mktpx": _fmt_price(rec["mktpx"]),
            "delta": _fmt_usd_or_na(rec["delta"]),
            "theta": _fmt_usd_or_na(rec["theta"]),
            "gamma": _fmt_usd_or_na(rec["gamma"]),
            "vega": _fmt_usd_or_na(rec["vega"]),
            "expiry": rec["expiry"] or "",
            "underlying": rec["underlying"] or "",
            "strike": _fmt_rate_blank(rec["strike"]),
            "undfwdpx": _fmt_rate_na(rec["undfwdpx"]),
            "rho": _fmt_usd_or_na(rec["rho"]),
        })

    style_data_conditional = [
        {"if": {"filter_query": "{level} = 'TOTAL'"},
         "fontWeight": "700", "borderTop": "2px solid var(--muted)"},
        {"if": {"filter_query": "{level} = 'ASSET_CLASS'"}, "fontWeight": "600"},
        {"if": {"filter_query": "{level} = 'ASSET_CLASS'", "column_id": "label"}, "paddingLeft": "8px"},
        {"if": {"filter_query": "{level} = 'PACKAGE'", "column_id": "label"}, "paddingLeft": "24px"},
        {"if": {"filter_query": "{level} = 'LEG'", "column_id": "label"},
         "paddingLeft": "44px", "color": "var(--muted)"},
    ]
    # An option with no strike on file cannot be priced: the whole row is flagged so it
    # is impossible to miss (user request 2026-09-18), and the terms editor below the
    # table is where the strike is typed in.
    style_data_conditional.append(
        {"if": {"filter_query": "{type} contains 'no strike'"},
         "backgroundColor": "rgba(178, 59, 59, 0.14)", "color": "var(--neg)", "fontWeight": "700"})
    for key in ("delta", "theta", "gamma", "vega", "rho", "mktval"):
        style_data_conditional += [
            {"if": {"filter_query": f"{{{key}}} contains '('", "column_id": key},
             "color": "var(--neg)", "fontWeight": "700"},
            {"if": {"filter_query": f"{{{key}}} != '' && {{{key}}} != 'n/a' && "
                                     f"!({{{key}}} contains '(')", "column_id": key},
             "color": "var(--pos)", "fontWeight": "700"},
            {"if": {"filter_query": f"{{{key}}} = 'n/a'", "column_id": key},
             "color": "var(--muted)", "fontStyle": "italic"},
        ]
    return records, style_data_conditional


def options_table(df: pd.DataFrame, collapsed: Optional[Iterable[str]] = None,
                   table_id: str = TABLE_ID) -> dash_table.DataTable:
    records, style_data_conditional = format_rows(df, collapsed)
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": COLUMN_LABELS.get(c, c.replace("_", " ").title()), "id": c}
                 for c in ALL_COLUMNS],
        hidden_columns=list(HIDDEN_COLUMNS),
        data=records,
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_cell_conditional=[{"if": {"column_id": "label"}, "textAlign": "left", "minWidth": "200px"},
                                {"if": {"column_id": "type"}, "textAlign": "left", "minWidth": "120px"},
                                {"if": {"column_id": "instrument"}, "textAlign": "left", "minWidth": "200px",
                                 "color": "var(--muted)"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=style_data_conditional,
        page_size=100,
        page_action="native",
        cell_selectable=True,
    )


TERMS_INSTRUMENT_ID = "options-terms-instrument"
TERMS_STRIKE_ID = "options-terms-strike"
TERMS_TYPE_ID = "options-terms-type"
TERMS_PAYOFF_ID = "options-terms-payoff"
TERMS_BARRIER_ID = "options-terms-barrier"
TERMS_SAVE_ID = "options-terms-save"
TERMS_STATUS_ID = "options-terms-status"


def option_instruments(conn: sqlite3.Connection) -> List[dict]:
    """Every option instrument with a trade on file, with its current terms; options
    with no strike first so the ones that block pricing are at the top of the list.
    Same missing-column defence as `_leg_rows` (`_instrument_options_columns`) -- this
    query hits the same pre-`payoff`-column dev DB via `terms_editor`."""
    opt_cols = _instrument_options_columns(conn)
    strike_expr = "COALESCE(o.strike, 0)" if "strike" in opt_cols else "0"
    option_type_expr = "COALESCE(o.option_type, '')" if "option_type" in opt_cols else "''"
    payoff_expr = "COALESCE(o.payoff, 'VANILLA')" if "payoff" in opt_cols else "'VANILLA'"
    barrier_expr = "COALESCE(o.barrier_level, 0)" if "barrier_level" in opt_cols else "0"
    rows = conn.execute(
        f"SELECT DISTINCT i.instrument_id, i.expiry_date, {strike_expr}, {option_type_expr}, "
        f"{payoff_expr}, {barrier_expr} "
        "FROM trades_official t JOIN instruments i USING (instrument_id) "
        "LEFT JOIN instrument_options o USING (instrument_id) "
        "WHERE t.product IN ('FX_OPTION','EQ_OPTION','CMDTY_OPTION') "
        f"ORDER BY ({strike_expr} = 0) DESC, i.expiry_date, i.instrument_id").fetchall()
    return [{"instrument_id": r[0], "expiry": r[1], "strike": r[2], "option_type": r[3],
             "payoff": r[4], "barrier_level": r[5]} for r in rows]


def terms_editor(conn: sqlite3.Connection) -> html.Details:
    """Option terms the blotter export cannot supply (2026-09-17): a digital's strike,
    a barrier level, or a payoff the free text did not name. Saved into
    `instrument_options` via `engine.options.store.set_option_terms`; a re-upload of the
    blotter never overwrites a value typed here. The next pricing run (live feed) uses
    them. Options with no strike on file are listed first and marked."""
    insts = option_instruments(conn)
    missing = [i for i in insts if not i["strike"]]
    options = [{"label": (f"{i['instrument_id']}  (exp {i['expiry']}" + (", NO STRIKE" if not i["strike"] else "") + ")"),
                "value": i["instrument_id"]} for i in insts]
    summary_text = "Option terms" + (f" -- {len(missing)} option(s) cannot be priced until their strike is entered"
                                      if missing else "")
    return html.Details(className="section options-terms", open=bool(missing), children=[
        html.Summary(summary_text),
        html.P("The blotter export carries no strike, barrier or payoff type for some options "
               "(typically digitals). Enter them here once; they survive re-uploads and feed the "
               "next pricing run.", className="section-kicker"),
        html.Div(className="toolbar", children=[
            html.Div(className="toolbar-group", children=[
                html.Label("Option"),
                dcc.Dropdown(id=TERMS_INSTRUMENT_ID, options=options,
                             value=(missing[0]["instrument_id"] if missing else (insts[0]["instrument_id"] if insts else None)),
                             clearable=False, style={"width": "360px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Payoff"),
                dcc.Dropdown(id=TERMS_PAYOFF_ID, clearable=False, style={"width": "150px"},
                             options=[{"label": PAYOFF_WORDS[k], "value": k} for k in PAYOFF_WORDS]),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Call / Put"),
                dcc.Dropdown(id=TERMS_TYPE_ID, clearable=False, style={"width": "110px"},
                             options=[{"label": "Call", "value": "CALL"}, {"label": "Put", "value": "PUT"}]),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Strike"),
                dcc.Input(id=TERMS_STRIKE_ID, type="number", step="any", style={"width": "120px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Barrier / touch level"),
                dcc.Input(id=TERMS_BARRIER_ID, type="number", step="any", style={"width": "120px"}),
            ]),
            html.Button("Save terms", id=TERMS_SAVE_ID, n_clicks=0, className="btn"),
            html.Span(id=TERMS_STATUS_ID, className="status-line", role="status"),
        ]),
    ])


def build_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole Options sub-tab body: Portfolio Totals -> asset class -> package ->
    leg, multi-leg packages collapsed by default, then the option-terms editor."""
    df = option_rows(conn, as_of)
    collapsed = default_collapsed_packages(df)
    return html.Div(className="section", children=[
        dcc.Store(id=COLLAPSED_STORE_ID, data=collapsed),
        options_table(df, collapsed),
        terms_editor(conn),
    ])


def register_callbacks(app, get_db_path: Callable[[], object],
                        date_picker_id: str = DEFAULT_DATE_PICKER_ID) -> None:
    """Expand/collapse: clicking a multi-leg PACKAGE row toggles its package_id in
    `COLLAPSED_STORE_ID`; a second callback re-runs `option_rows` for the current
    as-of date and re-renders (`format_rows` drops the now-collapsed/shows the
    now-expanded LEG rows) -- see module docstring for why this re-queries rather
    than filtering cached client-side data."""

    @app.callback(
        Output(COLLAPSED_STORE_ID, "data"),
        Input(TABLE_ID, "active_cell"),
        State(TABLE_ID, "data"),
        State(COLLAPSED_STORE_ID, "data"),
        prevent_initial_call=True,
    )
    def _toggle_package(active_cell, rows, collapsed):
        if not active_cell or not rows:
            return collapsed or []
        idx = active_cell.get("row")
        if idx is None or idx >= len(rows):
            return collapsed or []
        row = rows[idx]
        if row.get("level") != "PACKAGE" or not row.get("leg_count") or row["leg_count"] <= 1:
            return collapsed or []
        pkg = row.get("group_key")
        current = set(collapsed or [])
        if pkg in current:
            current.discard(pkg)
        else:
            current.add(pkg)
        return sorted(current)

    @app.callback(
        Output(TABLE_ID, "data"),
        Output(TABLE_ID, "style_data_conditional"),
        Input(COLLAPSED_STORE_ID, "data"),
        State(date_picker_id, "date"),
        prevent_initial_call=True,
    )
    def _refresh_table(collapsed, as_of_date):
        from dash.exceptions import PreventUpdate
        if not as_of_date:
            raise PreventUpdate
        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError:
            raise PreventUpdate
        try:
            df = option_rows(conn, as_of_date)
        finally:
            conn.close()
        return format_rows(df, collapsed)

    @app.callback(
        Output(TERMS_PAYOFF_ID, "value"),
        Output(TERMS_TYPE_ID, "value"),
        Output(TERMS_STRIKE_ID, "value"),
        Output(TERMS_BARRIER_ID, "value"),
        Input(TERMS_INSTRUMENT_ID, "value"),
    )
    def _prefill_terms(instrument_id):
        """Show the terms currently on file for the chosen option."""
        from dash.exceptions import PreventUpdate
        if not instrument_id:
            raise PreventUpdate
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(get_db_path())
        except sqlite3.OperationalError:
            raise PreventUpdate
        try:
            row = next((i for i in option_instruments(conn) if i["instrument_id"] == instrument_id), None)
        finally:
            conn.close()
        if row is None:
            raise PreventUpdate
        return (row["payoff"] or "VANILLA", row["option_type"] or None,
                row["strike"] or None, row["barrier_level"] or None)

    @app.callback(
        Output(TERMS_STATUS_ID, "children"),
        Output(COLLAPSED_STORE_ID, "data", allow_duplicate=True),
        Input(TERMS_SAVE_ID, "n_clicks"),
        State(TERMS_INSTRUMENT_ID, "value"),
        State(TERMS_PAYOFF_ID, "value"),
        State(TERMS_TYPE_ID, "value"),
        State(TERMS_STRIKE_ID, "value"),
        State(TERMS_BARRIER_ID, "value"),
        State(COLLAPSED_STORE_ID, "data"),
        prevent_initial_call=True,
    )
    def _save_terms(n_clicks, instrument_id, payoff, option_type, strike, barrier, collapsed):
        """Write the terms and re-render the grid (re-setting the collapse store fires
        `_refresh_table`, so the Type / Strike columns update at once)."""
        from dash import no_update
        if not n_clicks or not instrument_id:
            return no_update, no_update
        from data.ingest.schema import connect
        from engine.options.store import set_option_terms
        try:
            conn = connect(get_db_path())
            try:
                set_option_terms(conn, instrument_id, strike or 0.0, option_type or "", payoff or "VANILLA", barrier or 0.0)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            return html.Span(f"Not saved: {exc}", className="source-result--error"), no_update
        what = f"{PAYOFF_WORDS.get(payoff, payoff)} {(option_type or '').title()}, strike {strike}"
        return (html.Span(f"Saved {instrument_id}: {what}. Priced on the next Bloomberg cycle.",
                          className="source-result--info"), list(collapsed or []))
