"""Manual entry sub-tab of the Blotter (user request 2026-09-18: "need a place to
manually input OTC products - like the options that show up red").

One place for everything the blotter export does not carry:
  1. **Option terms** for options the export lists without a strike / payoff (the rows
     flagged red under Options) -- the same `ui.tabs.options.terms_editor` component,
     shown here as well as under Options (only one sub-tab body is rendered at a time,
     so the shared component ids never coexist).
  2. **Book an OTC trade by hand** -- an FX option (vanilla, digital, barrier, touch ...),
     an FX forward/spot or an FX swap (a hedge roll: one amount, a near and a far date,
     each with its own rate; `data.ingest.manual.book_fx_swap` writes it as two MANUAL
     trades sharing a `SWAP-` package) the export does not list at all, written through
     `data.ingest.manual` as `trades.source = 'MANUAL'` with the same instrument /
     trade / leg shape the blotter parser produces, so it prices, hedges and shows in
     every view like any other trade. A blotter re-upload never removes it.
  3. **Manual trades on file** -- the list, with a delete control (the only way a
     MANUAL trade leaves the book). A swap's two trades are listed with their package, and
     deleting either removes both (`delete_manual_trade` returns every id it removed).

Writes go through `data.ingest.schema.connect` (writable), never the read-only handle
the view callbacks use. A booking asks nothing of Bloomberg (2026-09-21: pulls are on
request only); the new trade's needs join the Bloomberg library and the next "Pull
Bloomberg now" prices it. Dates are typed as text and parsed tolerantly
(`data.ingest.manual._iso`: ISO, 25/11/2026, 20261125, 25 Nov 2026) per the
"never reject over formatting" rule; only an impossible trade is refused, with the
plain-sentence ValueError shown next to the button.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, List

from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs import options as options_ui
from ui.tabs import ranking as rk

PRODUCT_ID = "manual-product"
PAIR_ID = "manual-pair"
SIDE_ID = "manual-side"
TRADE_DATE_ID = "manual-trade-date"
COUNTERPARTY_ID = "manual-counterparty"
OPTION_FIELDS_ID = "manual-option-fields"
FORWARD_FIELDS_ID = "manual-forward-fields"
OPTION_TYPE_ID = "manual-option-type"
PAYOFF_ID = "manual-payoff"
STRIKE_ID = "manual-strike"
BARRIER_ID = "manual-barrier"
EXPIRY_ID = "manual-expiry"
NOTIONAL_ID = "manual-notional"
PREMIUM_ID = "manual-premium"
AMOUNT_ID = "manual-amount"
RATE_ID = "manual-rate"
VALUE_DATE_ID = "manual-value-date"
OUTRIGHT_FIELDS_ID = "manual-outright-fields"
SWAP_FIELDS_ID = "manual-swap-fields"
NEAR_DATE_ID = "manual-near-date"
NEAR_RATE_ID = "manual-near-rate"
FAR_DATE_ID = "manual-far-date"
FAR_RATE_ID = "manual-far-rate"
BOOK_ID = "manual-book"
BOOK_STATUS_ID = "manual-book-status"
LIST_ID = "manual-list"
TABLE_ID = "manual-datatable"
DELETE_PICK_ID = "manual-delete-pick"
DELETE_ID = "manual-delete"
DELETE_STATUS_ID = "manual-delete-status"

PRODUCT_OPTIONS = [{"label": "FX option", "value": "FX_OPTION"},
                   {"label": "FX forward / spot", "value": "FX_FWD"},
                   {"label": "FX swap", "value": "FX_SWAP"}]
# The products that use the base-amount group (FORWARD_FIELDS_ID): the forward's outright
# fields or the swap's near / far fields sit inside it, switched by `_toggle_swap_fields`.
_AMOUNT_PRODUCTS = ("FX_FWD", "FX_SWAP")
_SHOWN_INLINE = {"display": "contents"}   # the inner group's fields flow in the parent toolbar
_HIDDEN = {"display": "none"}
SIDE_OPTIONS = [{"label": "Buy", "value": "BUY"}, {"label": "Sell", "value": "SELL"}]

_LIST_COLUMNS = [("trade_id", "Trade id"), ("product", "Product"), ("instrument_id", "Instrument"),
                 ("trade_date", "Trade date"), ("side", "Side"), ("amount", "Amount"), ("price", "Fill"),
                 ("settle_date", "Settle / expiry"), ("terms", "Terms"), ("package", "Package"),
                 ("counterparty", "Counterparty")]
_PRODUCT_WORDS = {"FX_OPTION": "Option", "FX_FWD": "Forward", "FX_SWAP": "Swap"}


def _today_ny() -> str:
    from ui.tabs.cash_ladder import calendar_today_ny
    return calendar_today_ny()   # a trade is dated the calendar day it was dealt, no 17:00 roll


def _field(label: str, component) -> html.Div:
    return html.Div(className="toolbar-group", children=[html.Label(label), component])


def booking_form() -> html.Div:
    """The "Book an OTC trade by hand" card. Product-specific fields sit in two groups
    whose visibility the `_toggle_fields` callback switches on the product picked."""
    payoff_options = [{"label": options_ui.PAYOFF_WORDS[k], "value": k} for k in options_ui.PAYOFF_WORDS]
    return html.Div(className="section manual-entry", children=[
        html.H4("Book an OTC trade by hand"),
        html.P("For trades the blotter export does not carry (an option booked at another venue, a "
               "forward dealt outside the prime broker). Saved as source MANUAL: a blotter re-upload "
               "never removes it; delete it below when it is wrong. Priced the next time you press Pull Bloomberg now.",
               className="section-kicker"),
        html.Div(className="toolbar", children=[
            _field("Product", dcc.Dropdown(id=PRODUCT_ID, options=PRODUCT_OPTIONS, value="FX_OPTION",
                                           clearable=False, style={"width": "170px"})),
            _field("Pair", dcc.Input(id=PAIR_ID, type="text", placeholder="USDJPY", style={"width": "110px"})),
            _field("Side (base ccy)", dcc.Dropdown(id=SIDE_ID, options=SIDE_OPTIONS, value="BUY",
                                                   clearable=False, style={"width": "100px"})),
            _field("Trade date", dcc.Input(id=TRADE_DATE_ID, type="text", value=_today_ny(),
                                           placeholder="YYYY-MM-DD", style={"width": "120px"})),
            _field("Counterparty", dcc.Input(id=COUNTERPARTY_ID, type="text", placeholder="optional",
                                             style={"width": "140px"})),
        ]),
        html.Div(id=OPTION_FIELDS_ID, className="toolbar", children=[
            _field("Call / Put", dcc.Dropdown(id=OPTION_TYPE_ID, clearable=False, style={"width": "100px"},
                                              options=[{"label": "Call", "value": "CALL"}, {"label": "Put", "value": "PUT"}],
                                              value="CALL")),
            _field("Payoff", dcc.Dropdown(id=PAYOFF_ID, options=payoff_options, value="VANILLA",
                                          clearable=False, style={"width": "150px"})),
            _field("Strike", dcc.Input(id=STRIKE_ID, type="number", step="any", style={"width": "110px"})),
            _field("Barrier / touch level", dcc.Input(id=BARRIER_ID, type="number", step="any",
                                                      placeholder="barrier payoffs", style={"width": "120px"})),
            _field("Expiry", dcc.Input(id=EXPIRY_ID, type="text", placeholder="YYYY-MM-DD", style={"width": "120px"})),
            _field("Notional (base ccy)", dcc.Input(id=NOTIONAL_ID, type="number", step="any",
                                                    placeholder="1000000", style={"width": "130px"})),
            _field("Premium (fraction of notional)", dcc.Input(id=PREMIUM_ID, type="number", step="any",
                                                               placeholder="0.0125", style={"width": "130px"})),
        ]),
        html.Div(id=FORWARD_FIELDS_ID, className="toolbar", style={"display": "none"}, children=[
            _field("Amount (base ccy)", dcc.Input(id=AMOUNT_ID, type="number", step="any",
                                                  placeholder="1000000", style={"width": "130px"})),
            html.Div(id=OUTRIGHT_FIELDS_ID, style=_SHOWN_INLINE, children=[
                _field("Rate (outright)", dcc.Input(id=RATE_ID, type="number", step="any",
                                                    placeholder="147.25", style={"width": "120px"})),
                _field("Value date", dcc.Input(id=VALUE_DATE_ID, type="text", placeholder="YYYY-MM-DD",
                                               style={"width": "120px"})),
            ]),
            # FX swap: Side is the base currency on the near date, reversed on the far date.
            html.Div(id=SWAP_FIELDS_ID, style=_HIDDEN, children=[
                _field("Near date", dcc.Input(id=NEAR_DATE_ID, type="text", placeholder="YYYY-MM-DD",
                                              style={"width": "120px"})),
                _field("Near rate", dcc.Input(id=NEAR_RATE_ID, type="number", step="any",
                                              placeholder="147.10", style={"width": "120px"})),
                _field("Far date", dcc.Input(id=FAR_DATE_ID, type="text", placeholder="YYYY-MM-DD",
                                             style={"width": "120px"})),
                _field("Far rate", dcc.Input(id=FAR_RATE_ID, type="number", step="any",
                                             placeholder="146.40", style={"width": "120px"})),
            ]),
        ]),
        html.Div(className="toolbar", children=[
            html.Button("Book trade", id=BOOK_ID, n_clicks=0, className="btn"),
            html.Span(id=BOOK_STATUS_ID, className="status-line", role="status"),
        ]),
    ])


def _swap_dates(rows: List[dict]) -> dict:
    """trade_id -> 'Near date' / 'Far date' for the FX swaps listed: within a package the
    trade settling first is the near date (manual_trades gives each trade's first-leg date)."""
    packages: dict = {}
    for r in rows:
        if r["product"] == "FX_SWAP" and r.get("package_id"):
            packages.setdefault(r["package_id"], []).append(r)
    out = {}
    for members in packages.values():
        members = sorted(members, key=lambda r: (r["settle_date"], r["trade_id"]))
        for i, r in enumerate(members):
            out[r["trade_id"]] = "Near date" if i == 0 else "Far date"
    return out


def _package(r: dict) -> str:
    """The package a trade belongs to; '' for a trade that is its own package."""
    package_id = r.get("package_id") or ""
    return package_id if package_id != r["trade_id"] else ""


def _list_records(rows: List[dict]) -> List[dict]:
    records = []
    swap_dates = _swap_dates(rows)
    for r in rows:
        if r["product"] == "FX_SWAP":
            terms = swap_dates.get(r["trade_id"], "")
        elif r["product"] == "FX_OPTION":
            terms = f"{(r['payoff'] or 'VANILLA').title()} {(r['option_type'] or '').title()}"
            if r["strike"]:
                terms += f" strike {r['strike']:g}"
            if r["barrier_level"]:
                terms += f" barrier {r['barrier_level']:g}"
        else:
            terms = ""
        records.append({
            "trade_id": r["trade_id"], "product": _PRODUCT_WORDS.get(r["product"], r["product"]),
            "instrument_id": r["instrument_id"], "trade_date": r["trade_date"],
            "side": "Buy" if r["quantity"] >= 0 else "Sell", "amount": abs(round(float(r["quantity"]))),
            "price": rk.value(r["price"]),   # numbers (ui.tabs.ranking): the table prints them
            "settle_date": r["settle_date"], "terms": terms, "package": _package(r),
            "counterparty": r["counterparty"],
        })
    return records


def _delete_label(r: dict) -> str:
    label = f"{r['trade_id']}  {r['instrument_id']}"
    package = _package(r)
    return f"{label}  (swap {package}: both dates go)" if package else label


def manual_list(conn: sqlite3.Connection) -> html.Div:
    """The "Manual trades on file" card: a table plus a pick-and-delete control."""
    from data.ingest.manual import manual_trades
    rows = manual_trades(conn)
    if not rows:
        body = [html.P("No manual trades on file.", className="section-kicker")]
    else:
        body = [dash_table.DataTable(
            id=TABLE_ID,
            columns=[rk.numeric(name, col, rk.count()) if col == "amount"
                     else rk.numeric(name, col, rk.rate(6, trim=True)) if col == "price"
                     else rk.text(name, col) for col, name in _LIST_COLUMNS],
            data=_list_records(rows),
            **rk.sortable(TABLE_ID),
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "left", "fontFamily": "monospace", "padding": "4px 8px"},
            style_header={"fontWeight": "bold"},
        )]
    body.append(html.Div(className="toolbar", children=[
        _field("Delete a manual trade", dcc.Dropdown(
            id=DELETE_PICK_ID, style={"width": "260px"},
            options=[{"label": _delete_label(r), "value": r["trade_id"]} for r in rows],
            value=rows[0]["trade_id"] if rows else None, clearable=False, disabled=not rows)),
        html.Button("Delete", id=DELETE_ID, n_clicks=0, className="btn btn--ghost", disabled=not rows),
        html.Span(id=DELETE_STATUS_ID, className="status-line", role="status"),
    ]))
    return html.Div(className="section section--secondary", children=[
        html.H4(f"Manual trades on file ({len(rows)})"), *body])


def build_layout(conn: sqlite3.Connection) -> html.Div:
    """Terms editor (options the export left incomplete) first -- that is what the red
    banner sends people here for -- then the booking form, then the list."""
    return html.Div([
        options_ui.terms_editor(conn),
        booking_form(),
        html.Div(id=LIST_ID, children=manual_list(conn)),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    from dash import no_update

    @app.callback(
        Output(OPTION_FIELDS_ID, "style"),
        Output(FORWARD_FIELDS_ID, "style"),
        Input(PRODUCT_ID, "value"),
    )
    def _toggle_fields(product):
        if product in _AMOUNT_PRODUCTS:
            return {"display": "none"}, {}
        return {}, {"display": "none"}

    @app.callback(
        Output(OUTRIGHT_FIELDS_ID, "style"),
        Output(SWAP_FIELDS_ID, "style"),
        Input(PRODUCT_ID, "value"),
    )
    def _toggle_swap_fields(product):
        if product == "FX_SWAP":
            return _HIDDEN, _SHOWN_INLINE
        return _SHOWN_INLINE, _HIDDEN

    def _writable():
        from data.ingest.schema import connect
        return connect(get_db_path())

    @app.callback(
        Output(BOOK_STATUS_ID, "children"),
        Output(LIST_ID, "children"),
        Input(BOOK_ID, "n_clicks"),
        State(PRODUCT_ID, "value"), State(PAIR_ID, "value"), State(SIDE_ID, "value"),
        State(TRADE_DATE_ID, "value"), State(COUNTERPARTY_ID, "value"),
        State(OPTION_TYPE_ID, "value"), State(PAYOFF_ID, "value"), State(STRIKE_ID, "value"),
        State(BARRIER_ID, "value"), State(EXPIRY_ID, "value"), State(NOTIONAL_ID, "value"),
        State(PREMIUM_ID, "value"),
        State(AMOUNT_ID, "value"), State(RATE_ID, "value"), State(VALUE_DATE_ID, "value"),
        State(NEAR_DATE_ID, "value"), State(NEAR_RATE_ID, "value"),
        State(FAR_DATE_ID, "value"), State(FAR_RATE_ID, "value"),
        prevent_initial_call=True,
    )
    def _book(n_clicks, product, pair, side, trade_date, counterparty, option_type, payoff, strike,
              barrier, expiry, notional, premium, amount, rate, value_date,
              near_date=None, near_rate=None, far_date=None, far_rate=None):
        if not n_clicks:
            return no_update, no_update
        from data.ingest import manual
        if product == "FX_SWAP":
            blank = _blank_swap_fields(amount, near_date, near_rate, far_date, far_rate)
            if blank:
                verb = "are" if " and " in blank else "is"
                return (html.Span(f"Not booked: the {blank} {verb} blank.", className="source-result--error"),
                        no_update)
        try:
            conn = _writable()
            try:
                if product == "FX_SWAP":
                    near_id, far_id = manual.book_fx_swap(
                        conn, pair=pair, side=side, base_amount=amount, near_rate=near_rate, far_rate=far_rate,
                        near_date=near_date, far_date=far_date, trade_date=trade_date, counterparty=counterparty)
                    package = conn.execute("SELECT package_id FROM trades WHERE trade_id = ?",
                                           (near_id,)).fetchone()[0]   # ingest's package, never rebuilt here
                    trade_id = f"FX swap {package} (near date {near_id}, far date {far_id})"
                elif product == "FX_FWD":
                    trade_id = manual.book_fx_forward(
                        conn, pair=pair, side=side, base_amount=amount, rate=rate, value_date=value_date,
                        trade_date=trade_date, counterparty=counterparty)
                else:
                    trade_id = manual.book_fx_option(
                        conn, pair=pair, side=side, option_type=option_type, payoff=payoff, strike=strike,
                        barrier_level=barrier, expiry=expiry, notional=notional, premium=premium,
                        trade_date=trade_date, counterparty=counterparty)
                listing = manual_list(conn)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            return html.Span(f"Not booked: {exc}", className="source-result--error"), no_update
        # No Bloomberg pull here (2026-09-21: on request only). The new trade's needs are in
        # the Bloomberg library already; the next "Pull Bloomberg now" prices it.
        return (html.Span(f"Booked {trade_id}. It is in the book now; press Pull Bloomberg now to price it.",
                          className="source-result--info"), listing)

    @app.callback(
        Output(DELETE_STATUS_ID, "children"),
        Output(LIST_ID, "children", allow_duplicate=True),
        Input(DELETE_ID, "n_clicks"),
        State(DELETE_PICK_ID, "value"),
        prevent_initial_call=True,
    )
    def _delete(n_clicks, trade_id):
        if not n_clicks or not trade_id:
            return no_update, no_update
        from data.ingest import manual
        try:
            conn = _writable()
            try:
                removed = manual.delete_manual_trade(conn, trade_id)
                listing = manual_list(conn)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            return html.Span(f"Not deleted: {exc}", className="source-result--error"), no_update
        return html.Span(_deleted_words(trade_id, removed), className="source-result--info"), listing


def _blank_swap_fields(amount, near_date, near_rate, far_date, far_rate) -> str:
    """The swap fields left empty, in words ('near rate and far date'); '' when all are
    typed. Only emptiness is caught here; every other check is book_fx_swap's own."""
    named = (("amount", amount), ("near date", near_date), ("near rate", near_rate),
             ("far date", far_date), ("far rate", far_rate))
    blank = [name for name, value in named if value is None or str(value).strip() == ""]
    if len(blank) <= 1:
        return "".join(blank)
    return ", ".join(blank[:-1]) + " and " + blank[-1]


def _deleted_words(trade_id: str, removed) -> str:
    """'Deleted MANUAL-1.' or, for a swap, both trades named: deleting one date of a swap
    removes the other (data.ingest.manual.delete_manual_trade)."""
    removed = list(removed or [trade_id])
    if len(removed) == 1:
        return f"Deleted {removed[0]}."
    others = [t for t in removed if t != trade_id]
    return f"Deleted {trade_id} and {', '.join(others)}: both dates of the FX swap go together."
