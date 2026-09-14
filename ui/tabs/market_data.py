"""Bloomberg market-data diagnostics panel for the Cash ladder tab.

Pure formatting of data/bloomberg/live.py's status dict (what was requested, what came
back, what failed and why) plus the rates the ladder is actually using. Nothing here
fetches or invents prices.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from dash import dash_table, html

DIAG_ID = "bloomberg-diagnostics"
DIAG_TABLE_ID = "bloomberg-diagnostics-table"
DIAG_SUMMARY_ID = "bloomberg-diagnostics-summary"
RATES_TABLE_ID = "bloomberg-rates-table"

_MONO = {"fontFamily": "Consolas, 'Courier New', monospace", "fontSize": "12px", "padding": "3px 8px",
         "textAlign": "left", "whiteSpace": "nowrap"}
_HEAD = {"fontWeight": "600", "backgroundColor": "#f0f2f5", "borderBottom": "1px solid #d9dee3"}


def feed_headline(status: Optional[dict]) -> str:
    """One line for the toolbar / summary: connection, last pull, counts."""
    if not status:
        return "Bloomberg: no pull recorded yet"
    if not status.get("connected"):
        return f"Bloomberg: not connected — {status.get('reason', 'unknown reason')}"
    return (f"Bloomberg: connected · last pull {status.get('time', '')} · "
            f"{status.get('written', 0)} marks written, {status.get('failed', 0)} failed · refresh every 2 min")


def diagnostics_panel(status: Optional[dict], rates: Dict[str, dict], open_by_default: bool = False) -> html.Details:
    items: List[dict] = list((status or {}).get("items", []))
    failed = [i for i in items if i.get("status") != "OK"]
    rows = [{"status": i.get("status", ""), "instrument_id": i.get("instrument_id", ""),
             "mark_type": i.get("mark_type", ""), "settle_date": i.get("settle_date", ""),
             "value": "" if i.get("value") is None else f"{float(i['value']):.8f}",
             "source": i.get("source", ""), "detail": i.get("detail", "")} for i in items]
    rate_rows = [{"currency": c, "pair": v.get("pair", ""), "rate": f"{v['rate']:.8f}",
                  "inverted": "1/rate" if v["inverted"] else "direct", "source": v["source"],
                  "timestamp": v["timestamp"], "stale": "STALE" if v["stale"] else "fresh"}
                 for c, v in sorted(rates.items())]
    summary_bits = [feed_headline(status)]
    if status and status.get("as_of_date"):
        summary_bits.append(f"requests built for as-of {status['as_of_date']}; live marks stamped {status.get('as_of_marks', '')}")
    if status and status.get("warnings"):
        summary_bits.append(f"{len(status['warnings'])} warning(s) from the pull")
    children = [
        html.Summary(f"Bloomberg diagnostics · {len(items) - len(failed)} OK / {len(failed)} failed"
                     if items else "Bloomberg diagnostics"),
        html.Div(id=DIAG_SUMMARY_ID, className="status-line", children=" · ".join(summary_bits)),
    ]
    if status and status.get("traceback"):
        children.append(html.Pre(status["traceback"], className="diag-trace"))
    children += [
        html.H4("Requested marks: pulled vs failed"),
        dash_table.DataTable(
            id=DIAG_TABLE_ID,
            columns=[{"name": n, "id": i} for n, i in [("Status", "status"), ("Pair", "instrument_id"),
                                                       ("Mark", "mark_type"), ("Settle date", "settle_date"),
                                                       ("Value", "value"), ("Source", "source"), ("Detail", "detail")]],
            data=rows, page_size=40, sort_action="native", filter_action="native",
            style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
            style_data_conditional=[
                {"if": {"filter_query": "{status} = 'OK'", "column_id": "status"}, "color": "#1a7f4b", "fontWeight": "600"},
                {"if": {"filter_query": "{status} != 'OK'", "column_id": "status"}, "color": "#b42318", "fontWeight": "600"},
                {"if": {"filter_query": "{status} != 'OK'"}, "backgroundColor": "#fff4f2"},
            ]),
        html.H4("Spot rates the ladder is using (latest official SPOT mark per currency)"),
        dash_table.DataTable(
            id=RATES_TABLE_ID,
            columns=[{"name": n, "id": i} for n, i in [("Currency", "currency"), ("Pair", "pair"), ("Rate", "rate"),
                                                       ("USD per local", "inverted"), ("Source", "source"),
                                                       ("Snapped at", "timestamp"), ("Freshness", "stale")]],
            data=rate_rows, style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
            style_data_conditional=[{"if": {"filter_query": "{stale} = 'STALE'"}, "color": "#8a4b00",
                                     "backgroundColor": "#fff4e5"}]),
    ]
    if status and status.get("warnings"):
        children += [html.H4("Pull warnings"), html.Ul([html.Li(w, className="status-line") for w in status["warnings"]])]
    return html.Details(id=DIAG_ID, className="details details--diag", open=open_by_default or bool(failed), children=children)
