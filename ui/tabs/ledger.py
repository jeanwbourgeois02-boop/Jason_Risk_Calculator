"""P&L ledger block for the Cash ladder tab: realised / unrealised / total LTD, Daily,
5d, MTD, YTD, Trading. Pure formatting of engine.pnl.ledger.ledger_summary; every
figure carries its reference date, and anything the engine could not compute shows
'Unavailable' with the engine's reason. Nothing is estimated here.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd
from dash import dash_table, html

from ui.tabs.exposure import format_amount, format_date

LEDGER_ID = "pnl-ledger"
LEDGER_CARDS_ID = "pnl-ledger-cards"
LEDGER_NOTE_ID = "pnl-ledger-note"
REALISED_TABLE_ID = "pnl-ledger-realised"
SNAPSHOT_TABLE_ID = "pnl-ledger-snapshots"

_MONO = {"fontFamily": "Consolas, 'Courier New', monospace", "fontSize": "12px", "padding": "3px 8px",
         "textAlign": "right", "whiteSpace": "nowrap"}
_HEAD = {"fontWeight": "600", "backgroundColor": "#f0f2f5", "borderBottom": "1px solid #d9dee3"}


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _card(label: str, value, note: str = "", unavailable_reason: str = "") -> html.Div:
    if unavailable_reason or _nan(value):
        return html.Div([html.Span(label, className="card-label"),
                         html.Span("Unavailable", className="card-value card-value--muted"),
                         html.Span(unavailable_reason or "not computable", className="card-note")], className="card")
    return html.Div([html.Span(label, className="card-label"),
                     html.Span(format_amount(value), className="card-value"),
                     html.Span(note, className="card-note")], className="card")


def ledger_block(summary: Optional[dict], as_of_date: str) -> html.Div:
    """Cards + a note line + collapsed realised-trades and snapshot tables."""
    if summary is None:
        return html.Div(id=LEDGER_ID, className="section", children=[
            html.H4("P&L ledger"),
            html.P("Ledger unavailable: engine not reachable.", className="status-line")])
    missing = ", ".join(summary["missing"])
    unreal = summary["unrealisable"]
    p = summary["periods"]
    cards = [
        _card("Realised LTD", summary["realised_ltd_usd"], f"{summary['realised_trades']} settled trades frozen"),
        _card("Unrealised (open)", summary["unrealised_usd"], f"{summary['open_trades']} open trades at latest spot",
              f"missing rate: {missing}" if missing else ""),
        _card("Total LTD", summary["total_ltd_usd"],
              "realised + unrealised" + (f" · excludes {len(unreal)} settled trade(s) not realisable" if unreal else ""),
              f"missing rate: {missing}" if missing else ""),
        _card("Trading today", summary["trading_usd"], f"trades dated {as_of_date}",
              f"missing rate: {missing}" if missing else ""),
    ]
    for key, label in (("daily", "Daily P&L"), ("d5", "5-day P&L"), ("mtd", "MTD P&L"), ("ytd", "YTD P&L")):
        e = p[key]
        note = f"vs {format_date(e['snapshot_date'])}" if e["available"] else ""
        if e["available"] and e["reason"]:
            note += " (" + e["reason"] + ")"
        cards.append(_card(label, e["value"], note, "" if e["available"] else e["reason"]))
    last = summary.get("last_snapshot")
    note_bits = [f"As-of {as_of_date}"]
    note_bits.append(f"last snapshot {last['as_of_date']} at {last['snapped_at']}" + ("" if last["complete"] else " (incomplete)")
                     if last else "no snapshot stored yet: Daily/5d/MTD/YTD need a stored history")
    note_bits.append(f"{summary.get('snapshot_count', 0)} snapshot days")
    if unreal:
        note_bits.append("not realisable: " + ", ".join(f"{u['trade_id']} ({u['reason']})" for u in unreal[:5])
                         + (" ..." if len(unreal) > 5 else ""))
    realised = summary["realised_table"]
    realised_rows = []
    if isinstance(realised, pd.DataFrame) and not realised.empty:
        f = realised.copy()
        for col in ("local_amount", "usd_entry_amount", "pnl_usd"):
            f[col] = f[col].map(format_amount)
        f["spot_usd_per_local"] = f["spot_usd_per_local"].map(lambda v: f"{v:.6f}")
        realised_rows = f[["trade_id", "instrument_id", "currency", "settle_date", "local_amount", "usd_entry_amount",
                           "spot_usd_per_local", "spot_as_of_date", "spot_source", "pnl_usd", "note"]].to_dict("records")
    return html.Div(id=LEDGER_ID, className="section", children=[
        html.H4("P&L ledger · realised on settlement, unrealised at spot, periods from stored daily snapshots"),
        html.Div(id=LEDGER_CARDS_ID, className="cards", children=cards),
        html.Div(id=LEDGER_NOTE_ID, className="meta-line", children=[html.Span(b, className="meta-item") for b in note_bits]),
        html.Details(className="details", open=False, children=[
            html.Summary("Realised trades and snapshot history"),
            html.H4("Realised trades (frozen at the spot on their settle date)"),
            dash_table.DataTable(
                id=REALISED_TABLE_ID,
                columns=[{"name": n, "id": i} for n, i in [
                    ("Trade", "trade_id"), ("Pair", "instrument_id"), ("Ccy", "currency"), ("Settled", "settle_date"),
                    ("Local amount", "local_amount"), ("USD entry", "usd_entry_amount"), ("Spot used", "spot_usd_per_local"),
                    ("Spot date", "spot_as_of_date"), ("Source", "spot_source"), ("Realised P&L", "pnl_usd"), ("Note", "note")]],
                data=realised_rows, page_size=25, sort_action="native",
                style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD),
            html.H4("Snapshot history"),
            snapshot_table(summary.get("snapshot_table")),
        ]),
    ])


def snapshot_table(snaps: Optional[pd.DataFrame]) -> dash_table.DataTable:
    rows = []
    if isinstance(snaps, pd.DataFrame) and not snaps.empty:
        f = snaps.copy()
        for col in ("realised_ltd_usd", "unrealised_usd", "total_ltd_usd", "net_usd", "gross_usd", "trading_usd"):
            f[col] = f[col].map(format_amount)
        f["complete"] = f["complete"].map(lambda v: "complete" if v else "incomplete")
        rows = f.sort_values("as_of_date", ascending=False).to_dict("records")
    return dash_table.DataTable(
        id=SNAPSHOT_TABLE_ID,
        columns=[{"name": n, "id": i} for n, i in [
            ("Date", "as_of_date"), ("Snapped", "snapped_at"), ("Realised LTD", "realised_ltd_usd"),
            ("Unrealised", "unrealised_usd"), ("Total LTD", "total_ltd_usd"), ("Net USD", "net_usd"),
            ("Gross USD", "gross_usd"), ("Trading", "trading_usd"), ("Open", "open_trades"),
            ("Realised", "realised_trades"), ("Status", "complete"), ("Missing", "missing")]],
        data=rows, page_size=15, style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
        style_data_conditional=[{"if": {"filter_query": "{complete} = 'incomplete'"}, "color": "#8a4b00"}])
