"""Manual booking CLI for engine/rates_vol trades -- the only trade-feed path this
package has today (see ``__init__.py``'s "No live trade feed" caveat: no blotter
``Fin Type`` maps to a rate option yet, so this CLI IS the ingest path for now).

Usage
-----
    py -m engine.rates_vol.book --db <path> --trade-id T-1 \\
        --payoff SWAPTION --ccy USD --index SOFR --notional 100e6 --long \\
        --side PAYER --strike 0.04 --expiry 2031-08-17 \\
        --start 2031-08-17 --end 2036-08-17

Writes ``instruments`` (asset_class ``IRS_OPTION``), ``instrument_rate_options``,
``trades`` (product ``SWAPTION`` or ``CAP_FLOOR``, source ``'MANUAL'``, quantity signed
by ``--long``/``--short``) and one ``NOTIONAL`` ``trade_legs`` row -- the same leg shape
CLAUDE.md documents for ``FX_OPTION`` (1 NOTIONAL leg, ``rate = 0`` per the schema's own
"0 for NOTIONAL" convention -- the strike/fixed rate lives in
``instrument_rate_options.strike``, not on the leg). Refuses to overwrite an existing
``trade_id`` (or an ``instrument_id`` collision) -- never silently replaces a booked
trade; re-run with a different ``--trade-id`` (or delete the old one first) instead.

``instrument_id`` is derived as ``trade_id`` itself (one instrument per manually-booked
trade in this simple CLI -- no attempt to share one instrument across multiple option
trades the way a real exchange-listed instrument would).
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from typing import Optional

from .store import ensure_instrument_rate_options_table, write_instrument_rate_option

_PAYOFF_TO_PRODUCT = {
    "SWAPTION": "SWAPTION",
    "BERMUDAN_SWAPTION": "SWAPTION",
    "CAP": "CAP_FLOOR",
    "FLOOR": "CAP_FLOOR",
}


class BookingError(Exception):
    """A refusal to book (bad arguments, or trade_id/instrument_id already exists) --
    distinct from a genuine programmer error, so main() can report it cleanly instead of
    printing a traceback."""


def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m engine.rates_vol.book",
        description="Manually book a swaption / Bermudan swaption / cap / floor trade into this app's SQLite schema.",
    )
    p.add_argument("--db", required=True, help="path to the SQLite database")
    p.add_argument("--trade-id", required=True, dest="trade_id")
    p.add_argument("--payoff", required=True, choices=sorted(_PAYOFF_TO_PRODUCT))
    p.add_argument("--ccy", required=True, help="notional currency, e.g. USD")
    p.add_argument("--index", required=True, help="rate index, e.g. SOFR -- must match curve_quotes.\"index\"")
    p.add_argument("--notional", required=True, type=float, help="absolute notional, e.g. 100e6; sign comes from --long/--short")
    p.add_argument("--strike", required=True, type=float, help="fixed rate / cap-floor strike, decimal (0.04 = 4%%)")
    p.add_argument("--expiry", required=True, help="ISO date -- the option's own expiry (cap/floor: the strip's last payment date)")
    p.add_argument("--start", required=True, dest="underlying_start", help="ISO date -- underlying swap/accrual start")
    p.add_argument("--end", required=True, dest="underlying_end", help="ISO date -- underlying swap maturity / accrual end")
    p.add_argument("--side", choices=["PAYER", "RECEIVER"], default=None,
                    help="required for SWAPTION/BERMUDAN_SWAPTION; must be omitted for CAP/FLOOR")
    p.add_argument("--exercise-dates", default="", dest="exercise_dates",
                    help="';'-joined ISO dates; REQUIRED for BERMUDAN_SWAPTION, ignored otherwise")
    p.add_argument("--premium", type=float, default=0.0, help="premium fill, notional-fraction decimal (default 0.0 = not on file)")
    p.add_argument("--fixed-freq", default="", dest="fixed_freq")
    p.add_argument("--float-freq", default="", dest="float_freq")
    p.add_argument("--trade-date", default=None, dest="trade_date", help="ISO date, default today")
    p.add_argument("--account", default="MANUAL-BOOK")
    p.add_argument("--counterparty", default="MANUAL")
    p.add_argument("--strategy", default="")
    p.add_argument("--trader", default="MANUAL-CLI")
    p.add_argument("--description", default="")

    side_group = p.add_mutually_exclusive_group(required=True)
    side_group.add_argument("--long", action="store_true", help="quantity is positive (bought the option)")
    side_group.add_argument("--short", action="store_true", help="quantity is negative (sold the option)")

    return p.parse_args(argv)


def _validate(args: argparse.Namespace) -> None:
    for label, value in (("--expiry", args.expiry), ("--start", args.underlying_start), ("--end", args.underlying_end)):
        try:
            datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise BookingError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}: {exc}") from exc

    if args.payoff in ("SWAPTION", "BERMUDAN_SWAPTION"):
        if args.side is None:
            raise BookingError(f"--side PAYER|RECEIVER is required for --payoff {args.payoff}")
    else:  # CAP or FLOOR
        if args.side is not None:
            raise BookingError(f"--side is not applicable to --payoff {args.payoff} (cap/floor carries no payer/receiver)")

    if args.payoff == "BERMUDAN_SWAPTION" and not args.exercise_dates:
        raise BookingError(
            "--exercise-dates is required for --payoff BERMUDAN_SWAPTION -- booking a Bermudan with no "
            "staged exercise dates would only ever be skipped at pricing time (\"no exercise dates\"), "
            "never mechanically generated (see store.py)"
        )
    if args.exercise_dates:
        dates = args.exercise_dates.split(";")
        for d in dates:
            try:
                datetime.date.fromisoformat(d)
            except ValueError as exc:
                raise BookingError(f"--exercise-dates entry {d!r} is not an ISO date: {exc}") from exc

    if args.notional <= 0:
        raise BookingError(f"--notional must be > 0 (sign comes from --long/--short), got {args.notional!r}")


def book_trade(conn: sqlite3.Connection, args: argparse.Namespace) -> str:
    """Write instruments/instrument_rate_options/trades/trade_legs for one manually
    booked trade. Returns the derived instrument_id. Raises BookingError if trade_id (or
    the derived instrument_id) already exists -- never overwrites."""
    _validate(args)

    trade_id = args.trade_id
    instrument_id = trade_id  # see module docstring -- one instrument per manual trade

    existing_trade = conn.execute("SELECT 1 FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    if existing_trade is not None:
        raise BookingError(f"trade_id {trade_id!r} already exists -- refusing to overwrite")
    existing_instrument = conn.execute(
        "SELECT 1 FROM instruments WHERE instrument_id = ?", (instrument_id,)
    ).fetchone()
    if existing_instrument is not None:
        raise BookingError(f"instrument_id {instrument_id!r} already exists -- refusing to overwrite")

    product = _PAYOFF_TO_PRODUCT[args.payoff]
    option_type = args.side or ""
    quantity = args.notional if args.long else -args.notional
    trade_date = args.trade_date or datetime.date.today().isoformat()

    ensure_instrument_rate_options_table(conn)
    with conn:
        conn.execute(
            "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
            "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
            (instrument_id, "IRS_OPTION", args.ccy, args.ccy, 1.0, 0, instrument_id, args.expiry),
        )
        conn.execute(
            "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
            "price, account, counterparty, strategy, trader, description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, "MANUAL", instrument_id, product, trade_id, trade_date, quantity, args.premium,
             args.account, args.counterparty, args.strategy, args.trader, args.description),
        )
        # One NOTIONAL leg, mirroring CLAUDE.md's FX_OPTION leg shape: rate = 0 (the
        # schema's own "0 for NOTIONAL" convention -- the strike lives in
        # instrument_rate_options.strike, not on the leg), settles_cash = 0,
        # settle_date = the option's own expiry (matches store.py's mark settle_date).
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
            "settles_cash) VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_id, 1, "NOTIONAL", args.ccy, quantity, trade_date, args.expiry, 0.0, 0),
        )

    write_instrument_rate_option(
        conn, instrument_id, payoff=args.payoff, strike=args.strike, index=args.index,
        underlying_start=args.underlying_start, underlying_end=args.underlying_end,
        option_type=option_type, exercise_dates=args.exercise_dates,
        fixed_freq=args.fixed_freq, float_freq=args.float_freq,
    )
    return instrument_id


def main(argv: Optional[list] = None) -> int:
    from data.ingest.schema import connect

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    conn = connect(args.db)
    try:
        instrument_id = book_trade(conn, args)
    except BookingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"booked trade_id={args.trade_id!r} instrument_id={instrument_id!r} payoff={args.payoff} "
          f"quantity={'+' if args.long else '-'}{args.notional}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
