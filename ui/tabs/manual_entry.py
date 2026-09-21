"""Manual entry sub-tab of the Blotter (user request 2026-09-18: "need a place to
manually input OTC products - like the options that show up red").

One place for everything the blotter export does not carry:
  1. **Option terms** for options the export lists without a strike / payoff (the rows
     flagged red under Options) -- the same `ui.tabs.options.terms_editor` component,
     shown here as well as under Options (only one sub-tab body is rendered at a time,
     so the shared component ids never coexist).
  2. **Book an OTC trade by hand** -- an FX option (vanilla, digital, barrier, touch ...)
     or an FX forward/spot the export does not list at all, written through
     `data.ingest.manual` as `trades.source = 'MANUAL'` with the same instrument /
     trade / leg shape the blotter parser produces, so it prices, hedges and shows in
     every view like any other trade. A blotter re-upload never removes it.
  3. **Manual trades on file** -- the list, with a delete control (the only way a
     MANUAL trade leaves the book).

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
from ui.tabs.formatting import format_cell

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
BOOK_ID = "manual-book"
BOOK_STATUS_ID = "manual-book-status"
LIST_ID = "manual-list"
TABLE_ID = "manual-datatable"
DELETE_PICK_ID = "manual-delete-pick"
DELETE_ID = "manual-delete"
DELETE_STATUS_ID = "manual-delete-status"

PRODUCT_OPTIONS = [{"label": "FX option", "value": "FX_OPTION"},
                   {"label": "FX forward / spot", "value": "FX_FWD"}]
SIDE_OPTIONS = [{"label": "Buy", "value": "BUY"}, {"label": "Sell", "value": "SELL"}]

_LIST_COLUMNS = [("trade_id", "Trade id"), ("product", "Product"), ("instrument_id", "Instrument"),
                 ("trade_date", "Trade date"), ("side", "Side"), ("amount", "Amount"), ("price", "Fill"),
                 ("settle_date", "Settle / expiry"), ("terms", "Terms"), ("counterparty", "Counterparty")]


def _today_ny() -> str:
    from ui.tabs.cash_ladder import today_ny
    return today_ny()


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
            _field("Rate (outright)", dcc.Input(id=RATE_ID, type="number", step="any",
                                                placeholder="147.25", style={"width": "120px"})),
            _field("Value date", dcc.Input(id=VALUE_DATE_ID, type="text", placeholder="YYYY-MM-DD",
                                           style={"width": "120px"})),
        ]),
        html.Div(className="toolbar", children=[
            html.Button("Book trade", id=BOOK_ID, n_clicks=0, className="btn"),
            html.Span(id=BOOK_STATUS_ID, className="status-line", role="status"),
        ]),
    ])


def _list_records(rows: List[dict]) -> List[dict]:
    records = []
    for r in rows:
        if r["product"] == "FX_OPTION":
            terms = f"{(r['payoff'] or 'VANILLA').title()} {(r['option_type'] or '').title()}"
            if r["strike"]:
                terms += f" strike {r['strike']:g}"
            if r["barrier_level"]:
                terms += f" barrier {r['barrier_level']:g}"
        else:
            terms = ""
        records.append({
            "trade_id": r["trade_id"], "product": {"FX_OPTION": "Option", "FX_FWD": "Forward", "FX_SWAP": "Swap"}.get(r["product"], r["product"]),
            "instrument_id": r["instrument_id"], "trade_date": r["trade_date"],
            "side": "Buy" if r["quantity"] >= 0 else "Sell", "amount": f"{abs(round(float(r['quantity']))):,}",
            "price": format_cell(r["price"]) if r["product"] != "FX_OPTION" else f"{float(r['price']):g}",
            "settle_date": r["settle_date"], "terms": terms, "counterparty": r["counterparty"],
        })
    return records


def manual_list(conn: sqlite3.Connection) -> html.Div:
    """The "Manual trades on file" card: a table plus a pick-and-delete control."""
    from data.ingest.manual import manual_trades
    rows = manual_trades(conn)
    if not rows:
        body = [html.P("No manual trades on file.", className="section-kicker")]
    else:
        body = [dash_table.DataTable(
            id=TABLE_ID,
            columns=[{"name": name, "id": col} for col, name in _LIST_COLUMNS],
            data=_list_records(rows),
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "left", "fontFamily": "monospace", "padding": "4px 8px"},
            style_header={"fontWeight": "bold"},
        )]
    body.append(html.Div(className="toolbar", children=[
        _field("Delete a manual trade", dcc.Dropdown(
            id=DELETE_PICK_ID, style={"width": "260px"},
            options=[{"label": f"{r['trade_id']}  {r['instrument_id']}", "value": r["trade_id"]} for r in rows],
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
        if product == "FX_FWD":
            return {"display": "none"}, {}
        return {}, {"display": "none"}

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
        prevent_initial_call=True,
    )
    def _book(n_clicks, product, pair, side, trade_date, counterparty, option_type, payoff, strike,
              barrier, expiry, notional, premium, amount, rate, value_date):
        if not n_clicks:
            return no_update, no_update
        from data.ingest import manual
        try:
            conn = _writable()
            try:
                if product == "FX_FWD":
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
                manual.delete_manual_trade(conn, trade_id)
                listing = manual_list(conn)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            return html.Span(f"Not deleted: {exc}", className="source-result--error"), no_update
        return html.Span(f"Deleted {trade_id}.", className="source-result--info"), listing
