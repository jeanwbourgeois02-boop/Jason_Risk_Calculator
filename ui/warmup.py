"""Warm the screens' caches in the background, so the first click is as fast as the next ones.

User, 2026-09-29: "the site is super slow". Once warm every tab renders in well under half a
second; cold, the first Book after a start, an upload or a pull paid the one-time loads (the
research history, the contract universe, the spread templates) and the first pricing of every
date and every shared result. This module does that work on a daemon thread straight after
start-up and after each change of the database, by calling the SAME functions with the SAME
arguments as the tabs, so each tab's first render is a memo hit:

  1. research   `engine.risk.commodity_history.warm(book_db=<db>)`: the history, its lookups,
                the settlements of the book's roots
  2. universe   `data.contracts.load_roots()`, `engine.spreads.templates.load_templates()`
  3. value      `blotter_pricing.priced_value_book(conn, as_of)` (the shared filled reader)
  4. spreads    `shared_spreads(conn, as_of, filled=True)` (Book, header, P&L)
  5. trade book `shared_trade_book(conn, as_of)`
  6. book       `book.gather(conn, as_of)` (its period rows price the reference closes)
  7. header     `header._build_figures(conn, as_of)` inside the header's own `pricing_snapshot`,
                `header.needed_marks(conn, as_of)`
  8. series     `engine.pnl.series.daily_series(conn, as_of, value_fn=raw_value_book)` (P&L, header chart)
  9. pnl        `pnl.base(conn, as_of)`, `pnl.period(conn, as_of, pnl.DEFAULT_PERIOD)`
 10. curve      `shared_spreads(conn, as_of)` (the engine's own reader), `shared_curve(conn, as_of)`
 11. risk       `risk.gather(conn, as_of)`, `shared_trade_risk(conn, as_of, wait=True)`
 12. risk_folds `risk_folds.book_positions(conn, as_of)`, `risk.stress_result(conn, as_of)` (the
                currency and stress folds, computed when opened)
 13. pnl_periods `pnl.period(conn, as_of, choice)` for every choice but Custom
 14. book_panels `book.history(conn, data, trade)` for every open trade (a row's panel chart)
 15. data       `market_data.warm(conn, as_of)`: the Data tab's own memos
Since 2026-09-29 (S6) the daily series, the curve positions and the book positions read the one
shared valuation per date (`blotter_pricing.raw_value_book`), so steps 8 to 14 price no date twice.

The memos that also read the research app's database and the risk config carry those files'
(mtime_ns, size) in their key (`blotter_pricing.research_inputs_key`), so everything the warm-up
fills stays warm until the next write to the book, the research data or the config.

Read-only: every connection is `ui.app.connect_readonly`, nothing is written to the database,
and no figure changes (the caches are the ones the screens fill anyway, keyed on the database
file's mtime, so a warm-up of an older revision is simply never read). It never blocks the
server and never raises: each step's failure is recorded in `last_run` and the next step runs.

`start` and `after_change` share one worker: a call while a run is going sets a flag, and the
worker runs once more when it finishes, never two runs at once. A run that sees the database
change under it (a pull writing while it warms) runs again on the new revision.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

log = logging.getLogger(__name__)

_GUARD = threading.Lock()
_STATE = {"running": False, "pending": False, "db_path_fn": None, "as_of_fn": None}
MAX_RERUNS = 3            # a database changing on every run (a pull mid-write) is left to the next call

# The last completed run, for the Data tab's diagnostics: {started_at (UTC ISO), finished_at,
# db_path, as_of, seconds {step: s}, total, ok, reason, errors {step: sentence}, runs (count),
# research ('' when warmed, else why the research app's data is not available on this PC)}.
last_run: Dict[str, object] = {}
_RUNS = 0
_SAID: set = set()        # the "research not available" reasons already logged (once per process)


def status() -> dict:
    """A copy of `last_run`, plus whether a run is going now."""
    with _GUARD:
        out = dict(last_run)
        out["seconds"] = dict(last_run.get("seconds") or {})
        out["errors"] = dict(last_run.get("errors") or {})
        out["running"] = bool(_STATE["running"])
    return out


def status_line() -> tuple:
    """(line, hover) for the Data tab's Diagnostics: "Warm-up: 3.2 s at 16:41, ok" (this PC's
    local time, as the feed's times), the seconds per step and any failure on hover."""
    s = status()
    if not s.get("finished_at"):
        return ("Warm-up: running" if s["running"] else "Warm-up: not run yet"), ""
    try:
        at = dt.datetime.fromisoformat(str(s["finished_at"])).astimezone().strftime("%H:%M")
    except ValueError:
        at = str(s["finished_at"])
    errors = s.get("errors") or {}
    if s.get("ok"):
        outcome = "ok"
    elif errors:
        outcome = f"{len(errors)} step{'s' if len(errors) != 1 else ''} failed ({', '.join(sorted(errors))})"
    else:
        outcome = str(s.get("reason") or "not complete")
    line = f"Warm-up: {float(s.get('total') or 0):.1f} s at {at}, {outcome}" + (", running again" if s["running"] else "")
    steps = ", ".join(f"{k} {v:.1f} s" for k, v in (s.get("seconds") or {}).items() if k not in ("as_of", "trades"))
    hover = "; ".join(p for p in (
        f"Fills every screen's caches in the background after a start, an upload or a pull, for {s.get('as_of')}",
        steps, *(f"{k}: {v}" for k, v in sorted(errors.items())), str(s.get("reason") or "")) if p)
    return line, hover


def start(db_path_fn: Callable[[], object], as_of_fn: Callable[[], Optional[str]]) -> None:
    """Warm everything for today's as-of on a daemon thread (the app's start-up). Never blocks,
    never raises."""
    _request(db_path_fn, as_of_fn)


def after_change(db_path_fn: Callable[[], object], as_of_fn: Callable[[], Optional[str]]) -> None:
    """The same, after an upload lands or a pull's writes are published. Debounced: while a run
    is going this only asks for one more run when it ends."""
    _request(db_path_fn, as_of_fn)


def _request(db_path_fn, as_of_fn) -> None:
    try:
        with _GUARD:
            _STATE["db_path_fn"], _STATE["as_of_fn"] = db_path_fn, as_of_fn
            if _STATE["running"]:
                _STATE["pending"] = True
                return
            _STATE["running"], _STATE["pending"] = True, False
        threading.Thread(target=_worker, name="screens-warmup", daemon=True).start()
    except Exception:  # noqa: BLE001 -- a warm-up that cannot start costs speed, never the server
        with _GUARD:
            _STATE["running"] = False
        log.exception("warm-up could not start")


def _worker() -> None:
    reruns = 0
    try:
        while True:
            with _GUARD:
                db_path_fn, as_of_fn = _STATE["db_path_fn"], _STATE["as_of_fn"]
            changed = False
            try:
                changed = _run_once(db_path_fn, as_of_fn)
            except Exception:  # noqa: BLE001 -- never out of the thread
                log.exception("warm-up failed")
            with _GUARD:
                if _STATE["pending"]:              # asked again meanwhile: one more run
                    _STATE["pending"], reruns = False, 0
                elif changed and reruns < MAX_RERUNS:
                    reruns += 1
                else:
                    _STATE["running"] = False
                    return
    except BaseException:  # noqa: BLE001 -- the flag must never stay set
        with _GUARD:
            _STATE["running"] = False
        raise


def _signature(path: str) -> str:
    try:
        from ui.revision import file_signature
        return file_signature(path)
    except Exception:  # noqa: BLE001
        return ""


def _run_once(db_path_fn, as_of_fn) -> bool:
    """One pass over the steps. True when the database changed while it ran (run again)."""
    global _RUNS
    started = time.perf_counter()
    info: Dict[str, object] = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                               "seconds": {}, "errors": {}, "ok": False, "reason": ""}
    seconds: Dict[str, float] = info["seconds"]          # type: ignore[assignment]
    errors: Dict[str, str] = info["errors"]              # type: ignore[assignment]

    def step(name: str, fn: Callable[[], object]) -> object:
        t = time.perf_counter()
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- recorded; the tab will say why itself
            errors[name] = f"{type(exc).__name__}: {exc}"
            log.debug("warm-up step %s failed", name, exc_info=True)
            return None
        finally:
            seconds[name] = round(seconds.get(name, 0.0) + time.perf_counter() - t, 3)

    path = str(db_path_fn()) if db_path_fn is not None else ""
    as_of = step("as_of", as_of_fn) if as_of_fn is not None else None
    if as_of is None:
        from ui.tabs.controls import today_ny
        as_of = today_ny()
    info["db_path"], info["as_of"] = path, as_of
    before = _signature(path)
    changed = False
    if not path or not Path(path).exists():
        info["reason"] = f"no database at {path or '(none)'}"
    else:
        # The research app's database may simply not be on this PC: that is "not available", kept in
        # `research`, never a failed step; a real error while warming it is one.
        info["research"] = step("research", lambda: _research(path)) or ""
        if info["research"] and info["research"] not in _SAID:
            _SAID.add(info["research"])
            log.debug("warm-up: research not available (%s)", info["research"])
        step("universe", _universe)
        from ui.app import connect_readonly
        conn = connect_readonly(path)
        try:
            n = step("trades", lambda: conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]) or 0
            if not n:
                info["reason"] = "no trades on file: nothing to price"
            else:
                _screens(conn, as_of, step)
        finally:
            conn.close()
        changed = bool(before) and _signature(path) != before
        if changed:
            info["reason"] = "the database changed while warming: warmed again on the new revision"
    total = round(time.perf_counter() - started, 3)
    info["total"] = total
    info["ok"] = not errors and not changed and not info["reason"].startswith("no database")
    info["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _GUARD:
        _RUNS += 1
        info["runs"] = _RUNS
        last_run.clear()
        last_run.update(info)
    steps = ", ".join(f"{k} {v:.1f}s" for k, v in seconds.items() if k not in ("as_of", "trades"))
    # Quiet terminal (user, 2026-09-30): a run is DEBUG; only a real step failure is a WARNING.
    for name in sorted(errors):
        log.warning("Screens warm-up: step %s failed: %s", name, errors[name])
    log.debug("screens warm-up for %s: %.1fs (%s)%s", as_of, total, steps or "nothing to do",
              f"; {info['reason']}" if info["reason"] else "")
    return changed


def _research(path: str) -> str:
    """'' when warmed; the reason when the research app's data is not available on this PC (not a
    failure). A real error while warming (`warm`'s "warm-up stopped (...)") raises: a failed step."""
    from engine.risk.commodity_history import warm
    out = warm(book_db=path)
    reason = str(out.get("reason") or "")
    if out.get("ok") or not reason:
        return ""
    if reason.startswith("warm-up stopped ("):
        raise RuntimeError(reason)
    return reason


def _universe() -> None:
    from data.contracts import load_roots
    from engine.spreads.templates import load_templates
    load_roots()
    load_templates()


def _screens(conn, as_of: str, step) -> None:
    """The per-revision memos, in the order a user meets them: the Book (the app opens on it) and
    the header, then P&L, then Risk."""
    from ui.tabs import blotter_pricing as bp

    step("value", lambda: bp.priced_value_book(conn, as_of))
    step("spreads", lambda: bp.shared_spreads(conn, as_of, filled=True))
    step("trade_book", lambda: bp.shared_trade_book(conn, as_of))

    def book():
        from ui.tabs import book as book_tab
        book_tab.gather(conn, as_of)
        book_tab.price_checks(conn, as_of)      # the marks check's flags on the Book (the Data tab's memo too)
    step("book", book)

    def header():
        from ui.tabs import header as header_tab
        with bp.pricing_snapshot(conn):
            header_tab._build_figures(conn, as_of)
        header_tab.needed_marks(conn, as_of)
    step("header", header)

    def series():
        from engine.pnl.series import daily_series
        daily_series(conn, as_of, value_fn=bp.raw_value_book)   # the P&L tab's and the header chart's own call
    step("series", series)

    def pnl():
        from ui.tabs import pnl as pnl_tab
        pnl_tab.base(conn, as_of)
        pnl_tab.period(conn, as_of, pnl_tab.DEFAULT_PERIOD)
    step("pnl", pnl)

    def curve():
        bp.shared_spreads(conn, as_of)
        bp.shared_curve(conn, as_of)
    step("curve", curve)

    def risk():
        from ui.tabs import risk as risk_tab
        risk_tab.gather(conn, as_of)
        bp.shared_trade_risk(conn, as_of, wait=True)
    step("risk", risk)

    def risk_folds():
        # computed when a fold is opened: the currency fold's delta, the stress list
        from ui.tabs import risk as risk_tab
        from ui.tabs import risk_folds as rf
        rf.book_positions(conn, as_of)
        risk_tab.stress_result(conn, as_of)
    step("risk_folds", risk_folds)

    def risk_subsets():
        # the Risk headline's and the group rows' VaR (subset_var of the trades showing), unfiltered,
        # for each grouping of the switch
        from ui.tabs import risk as risk_tab
        from ui.tabs import trade_filter as tf
        data = risk_tab.gather(conn, as_of)
        risk = bp.shared_trade_risk(conn, as_of, wait=True)
        rows = [(t, r) for t, r in risk_tab.rows_of(data, risk) if r]
        risk_tab.subset(conn, as_of, [str(r.get("trade")) for _t, r in rows])
        for group in (tf.GROUP_BY_TYPE, tf.GROUP_BY_COMMODITY):
            by = {}
            for t, r in rows:
                by.setdefault(tf.group_of(t, group), []).append(str(r.get("trade")))
            for names in by.values():
                risk_tab.subset(conn, as_of, names)
    step("risk_subsets", risk_subsets)

    def pnl_periods():
        # every period of the P&L switch but Custom (MTD is warmed above)
        from ui.tabs import pnl as pnl_tab
        for choice, _label in pnl_tab.PERIOD_CHOICES:
            if choice not in ("custom", pnl_tab.DEFAULT_PERIOD):
                pnl_tab.period(conn, as_of, choice)
    step("pnl_periods", pnl_periods)

    def book_panels():
        # each open trade's level since its first fill (a row's panel), read on the shared valuation
        from ui.tabs import book as book_tab
        data = book_tab.gather(conn, as_of)
        for t in data.get("trades") or []:
            if t.get("status") == "open" and not t.get("pseudo"):
                book_tab.history(conn, data, t)
    step("book_panels", book_panels)

    def data_tab():
        from ui.tabs import market_data as md
        warm = getattr(md, "warm", None)
        if callable(warm):
            warm(conn, as_of)
    step("data", data_tab)
