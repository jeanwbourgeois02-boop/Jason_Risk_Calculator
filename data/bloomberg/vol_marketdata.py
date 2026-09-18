"""Bloomberg FX option volatility feed -- options_calc merge Phase 5.

Scope: a delta-smile per pair/tenor (ATM, 25-delta and 10-delta risk reversal /
butterfly), staged into a new ``vol_quotes`` table. This module does NOT build a
``FXDeltaVolSurface`` or feed ``engine/options`` directly -- that stays
options-pricer's job, reading the smile back out via ``vol_smile`` /
``atm_vol_for_expiry`` below. engine/options/inputs.py's own ``option_vols``
table (manual, flat placeholder) is untouched by this module.

Structure mirrors ``data/bloomberg/rates_marketdata.py`` exactly: two source
implementations behind one informal interface (``get_vol_quotes``):
  - ``VolBloombergSource``: live blpapi wrapper. Opens its OWN blpapi.Session
    (same deliberate non-sharing as RatesBloombergSource -- see that module's
    docstring; the same follow-up to merge sessions is tracked by the
    housekeeper, not addressed here). Requires the `blpapi` package (raises
    MarketDataError with an install hint if missing) and a running Bloomberg
    Terminal / B-PIPE. NEVER crashes on a per-ticker failure: every missing
    ticker is recorded in ``VolFetchResult.diagnostics`` instead, in the
    spirit of pull_marks.py's diagnostics (see that module's docstring).
  - ``VolFileSource``: reads a saved JSON snapshot (see
    data/bloomberg/fixtures/fx_vol_snapshot_v1.json) -- no blpapi dependency
    at all, used by tests and offline work.

Bloomberg is NOT reachable from this development machine. Every ticker/field
name and request-shape choice below is UNVERIFIED -- copied from ordinary FX
vol-surface convention, never checked against a live terminal. The user will
run ``--probe`` on the Bloomberg terminal to check these; tick off as each is
confirmed (mirrors docs/open-questions.md items 27/28 for pull_marks.py's
FWD_OUTRIGHT guesses):

  1. UNVERIFIED -- ATM ticker '<PAIR>V<TENOR> BGN Curncy' (e.g.
     'EURUSDV1M BGN Curncy').
  2. UNVERIFIED -- 25-delta risk reversal ticker
     '<PAIR>25R<TENOR> BGN Curncy' (e.g. 'EURUSD25R1M BGN Curncy').
  3. UNVERIFIED -- 25-delta butterfly ticker '<PAIR>25B<TENOR> BGN Curncy'.
  4. UNVERIFIED -- 10-delta risk reversal ticker '<PAIR>10R<TENOR> BGN Curncy'.
  5. UNVERIFIED -- 10-delta butterfly ticker '<PAIR>10B<TENOR> BGN Curncy'.
  6. UNVERIFIED -- field PX_LAST is correct for all five quote types (vs a
     bid/ask/mid-specific field, or BGN vs a specific contributor code).
  7. UNVERIFIED -- 'ON' (overnight) is a valid tenor suffix on these vol
     tickers, the same way it is for OIS depo tenors; may need to be dropped
     from VOL_TENORS if the terminal rejects it.
  8. UNVERIFIED -- whether a live ReferenceDataRequest (as_of=None) vs a
     HistoricalDataRequest (as_of given) is the right split for vol quotes,
     the same way it is for OIS quotes in rates_marketdata.py -- i.e.
     whether Bloomberg's vol-surface fields even support HistoricalDataRequest
     cleanly, or need a different override.
  9. UNVERIFIED -- values come back already in vol points with no separate
     scale field to read (unlike forward points, which need
     FWD_POINTS_SCALE) -- i.e. that PX_LAST really is "7.85" meaning 7.85%,
     not something requiring further conversion.

Since 2026-09-17, ``data/bloomberg/live.py``'s ``_vol_step`` requests every one of the
tickers above on every live pull with an open FX_OPTION in the book -- so a live pull now
doubles as this module's own probe, and the user no longer needs to run ``--probe`` by
hand for these to get checked. ``assess_ticker_assumptions`` / ``record_vol_ticker_checks``
below (2026-09-18) turn what Bloomberg actually returned into a persistent
``vol_ticker_checks`` table row per assumption (last checked time, ticker(s) tried,
outcome, value seen), derived entirely from the same ``VolFetchResult.diagnostics`` the
vol step already produces -- no extra Bloomberg request is made for this, except that the
scale assumption (item 9) is judged from the returned ATM value itself (7.5 vs 0.075).
``tools/bbg_diagnostics.py``'s "FX vol ticker assumptions" check reads that table instead
of unconditionally telling the user to run ``--probe``. The numbered UNVERIFIED list above
stays as documentation of what each assumption means and why it was originally a guess --
it is not removed just because a live pull may since have confirmed it.

Units: ``vol_quotes.value`` is stored RAW, in vol points exactly as Bloomberg
quotes them (e.g. 7.85 meaning 7.85%). The consumer (options-pricer) divides
by 100 before feeding a decimal vol into any pricer -- this module never
does that scaling itself, so the staging table is a faithful copy of what
Bloomberg returned (same "store raw, scale downstream" discipline as
rates_marketdata.py's OIS quotes, which ARE pre-scaled to decimal by
``scale_quote`` -- vol quotes deliberately are NOT, per this task's spec).

Writes go to the ``vol_quotes`` staging table, created defensively with
CREATE TABLE IF NOT EXISTS (see ``ensure_vol_quotes_table`` /
``write_vol_quotes``), same pattern as rates_marketdata.py's ``curve_quotes``.

CLI (see ``main`` docstring below for full usage and exit codes):
    py -m data.bloomberg.vol_marketdata --db risk.db --as-of 2026-09-17 --file data/bloomberg/fixtures/fx_vol_snapshot_v1.json
    py -m data.bloomberg.vol_marketdata --db risk.db --as-of 2026-09-17          # live (needs blpapi + terminal)
    py -m data.bloomberg.vol_marketdata --db risk.db --probe                    # ticker/field probe, run on the Bloomberg machine
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from engine.rates.store import snapped_at

# --------------------------------------------------------------------------- scope constants
# Standard FX vol-surface tenors. 'ON' is itself UNVERIFIED (probe item 7) -- included
# because it is the standard first vol-surface tenor on most single-currency-pair vendor
# feeds, but may need dropping if the terminal rejects it.
VOL_TENORS = ["ON", "1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y"]

# marks.mark_type-style enum for this table's quote_type column (CLAUDE.md "Data contract").
VOL_QUOTE_TYPES = ["ATM", "RR25", "BF25", "RR10", "BF10"]

VOL_FIELD = "PX_LAST"

# Pairs to pull when the book has no FX_OPTION instruments yet (see vol_pairs_needed).
DEFAULT_PAIRS = ["EURUSD", "EURSEK", "USDJPY"]


class MarketDataError(Exception):
    """Base error for this market data layer (mirrors rates_marketdata.MarketDataError;
    kept as its own class, not shared, so this module has no import dependency on
    rates_marketdata -- see module docstring "Structure mirrors ... exactly")."""


class MarketDataUnavailable(MarketDataError):
    """Requested vol data cannot be produced (wrong as_of, unknown pair in a file
    snapshot)."""


class TickerMapError(Exception):
    pass


# --------------------------------------------------------------------------- ticker map
# Built by FUNCTION (vol_ticker), not a hard-coded per-pair table, so a new pair is
# supported just by passing its name -- unlike rates_marketdata.OIS_CURVES, which is a
# per-currency literal ticker table because OIS ticker roots don't follow one formula
# across currencies. FX vol tickers do follow one formula (UNVERIFIED, see module
# docstring items 1-5), so a function suffices; FX_VOL_TICKERS below is the quote_type ->
# ticker-infix piece of that formula, not a per-pair map.
FX_VOL_TICKERS: Dict[str, str] = {
    "ATM": "V",
    "RR25": "25R",
    "BF25": "25B",
    "RR10": "10R",
    "BF10": "10B",
}


def normalize_pair(pair: str) -> str:
    return pair.strip().upper()


def normalize_tenor(tenor: str) -> str:
    return tenor.strip().upper()


def vol_ticker(pair: str, tenor: str, quote_type: str) -> str:
    """Build the (UNVERIFIED) Bloomberg ticker for one pair/tenor/quote_type -- see
    module docstring probe items 1-5 for the exact convention being guessed at."""
    infix = FX_VOL_TICKERS.get(quote_type)
    if infix is None:
        raise TickerMapError(
            f"Unknown vol quote_type {quote_type!r}; expected one of {sorted(FX_VOL_TICKERS)}"
        )
    return f"{normalize_pair(pair)}{infix}{normalize_tenor(tenor)} BGN Curncy"


def tenor_to_days(tenor: str) -> int:
    """Approximate a vol tenor string to a day count, for sorting/interpolation only
    (not accrual) -- same spirit as rates_marketdata.tenor_to_days, but with its own
    'ON' -> 1 day special case (rates_marketdata's OIS tenor set has no ON tenor, so
    that function doesn't need this)."""
    t = normalize_tenor(tenor)
    if t == "ON":
        return 1
    if not t:
        raise ValueError(f"Cannot parse tenor: {tenor!r}")
    unit = t[-1]
    try:
        number = int(t[:-1])
    except ValueError:
        raise ValueError(f"Cannot parse tenor: {tenor!r}")
    if unit == "D":
        return number
    if unit == "W":
        return number * 7
    if unit == "M":
        return number * 30
    if unit == "Y":
        return number * 365
    raise ValueError(f"Cannot parse tenor: {tenor!r}")


def vol_pairs_needed(conn: sqlite3.Connection) -> List[str]:
    """Distinct base_ccy||quote_ccy for instruments with asset_class = 'FX_OPTION',
    sorted. Falls back to DEFAULT_PAIRS when the book has no FX_OPTION instruments yet
    (empty result) or the instruments table doesn't exist yet (fresh/partial schema) --
    never raises."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT base_ccy || quote_ccy FROM instruments WHERE asset_class = 'FX_OPTION'"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    pairs = sorted({r[0] for r in rows if r[0]})
    return pairs if pairs else list(DEFAULT_PAIRS)


# --------------------------------------------------------------------------- dataclasses

def _date_to_str(value: Optional[date]) -> Optional[str]:
    return None if value is None else value.isoformat()


def _str_to_date(value: Optional[str]) -> Optional[date]:
    return None if value is None else date.fromisoformat(value)


@dataclass
class VolQuote:
    tenor: str
    quote_type: str
    ticker: str
    value: float
    field: str = VOL_FIELD
    source: str = "BBG"

    def __post_init__(self) -> None:
        self.tenor = normalize_tenor(self.tenor)

    def to_dict(self) -> dict:
        return {
            "tenor": self.tenor, "quote_type": self.quote_type, "ticker": self.ticker,
            "value": self.value, "field": self.field, "source": self.source,
        }

    @staticmethod
    def from_dict(data: dict) -> "VolQuote":
        return VolQuote(
            tenor=data["tenor"], quote_type=data["quote_type"], ticker=data["ticker"],
            value=float(data["value"]), field=data.get("field", VOL_FIELD),
            source=data.get("source", "BBG"),
        )


@dataclass
class PairVolSnapshot:
    pair: str
    as_of: date
    quotes: List[VolQuote] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pair": self.pair, "as_of": _date_to_str(self.as_of),
            "quotes": [q.to_dict() for q in self.quotes],
        }

    @staticmethod
    def from_dict(data: dict) -> "PairVolSnapshot":
        return PairVolSnapshot(
            pair=data["pair"], as_of=_str_to_date(data["as_of"]),
            quotes=[VolQuote.from_dict(q) for q in data.get("quotes", [])],
        )


@dataclass
class VolSnapshot:
    """On-disk shape for VolFileSource -- one as_of, many pairs. Mirrors
    rates_marketdata.OisSnapshot (currency -> pair)."""
    as_of: date
    source: str
    pairs: Dict[str, PairVolSnapshot] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "as_of": _date_to_str(self.as_of), "source": self.source,
            "pairs": {k: v.to_dict() for k, v in self.pairs.items()},
        }

    @staticmethod
    def from_dict(data: dict) -> "VolSnapshot":
        return VolSnapshot(
            as_of=_str_to_date(data["as_of"]), source=data["source"],
            pairs={k: PairVolSnapshot.from_dict(v) for k, v in data.get("pairs", {}).items()},
        )


@dataclass
class VolFetchResult:
    """Returned by both sources' get_vol_quotes. `diagnostics` carries one entry per
    ticker that could not be resolved -- callers (the CLI, tests) inspect this instead
    of an exception; a missing ticker never raises (see module docstring)."""
    as_of: date
    source: str
    pairs: Dict[str, PairVolSnapshot] = field(default_factory=dict)
    diagnostics: List[dict] = field(default_factory=list)


def load_vol_snapshot(path: Union[str, Path]) -> VolSnapshot:
    with open(path, "r", encoding="utf-8") as f:
        return VolSnapshot.from_dict(json.load(f))


def save_vol_snapshot(snap: VolSnapshot, path: Union[str, Path]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------- VolFileSource

class VolFileSource:
    """FileSource-equivalent for vol quotes: never touches blpapi, reads a saved
    VolSnapshot (JSON path or already-loaded). Mirrors RatesFileSource."""

    def __init__(self, snapshot_or_path: Union[VolSnapshot, str, Path]) -> None:
        if isinstance(snapshot_or_path, VolSnapshot):
            self._snapshot = snapshot_or_path
        else:
            self._snapshot = load_vol_snapshot(snapshot_or_path)

    def name(self) -> str:
        return f"VolFileSource({self._snapshot.source})"

    def get_vol_quotes(
        self, pairs: Sequence[str], as_of: Optional[date] = None,
        tenors: Optional[Sequence[str]] = None,
    ) -> VolFetchResult:
        """`tenors` is accepted for interface parity with VolBloombergSource (used by
        --probe to narrow to one tenor) but has no effect here: a file snapshot already
        contains whichever tenors it contains, filtering happens naturally through
        vol_smile/atm_vol_for_expiry downstream, not by trimming what's returned."""
        if as_of is not None and as_of != self._snapshot.as_of:
            raise MarketDataUnavailable(
                f"Snapshot is as of {self._snapshot.as_of}, requested {as_of}"
            )
        diagnostics: List[dict] = []
        pairs_out: Dict[str, PairVolSnapshot] = {}
        for pair in pairs:
            key = normalize_pair(pair)
            snap = self._snapshot.pairs.get(key)
            if snap is None:
                diagnostics.append({
                    "pair": key, "status": "MISSING",
                    "detail": f"no snapshot for pair; available: {sorted(self._snapshot.pairs)}",
                })
                continue
            pairs_out[key] = snap
        return VolFetchResult(
            as_of=self._snapshot.as_of, source=self._snapshot.source,
            pairs=pairs_out, diagnostics=diagnostics,
        )


# --------------------------------------------------------------------------- VolBloombergSource

_MAX_CONSECUTIVE_TIMEOUTS = 3


def _bbg_error_message(el, key: str) -> Optional[str]:
    """`el.<key>`'s message text if present, else None (e.g. key="securityError").
    Deliberately a small local helper, not imported from pull_marks.py's equivalent --
    see module docstring "Structure mirrors ... exactly" / "kept as its own class, not
    shared, so this module has no import dependency on rates_marketdata" (same reasoning
    extends to pull_marks.py here)."""
    if not el.hasElement(key):
        return None
    sub = el.getElement(key)
    return sub.getElementAsString("message") if sub.hasElement("message") else key


def _bbg_field_exception_message(el, field_name: str) -> Optional[str]:
    if not el.hasElement("fieldExceptions"):
        return None
    fx_el = el.getElement("fieldExceptions")
    for j in range(fx_el.numValues()):
        fx = fx_el.getValueAsElement(j)
        fid = fx.getElementAsString("fieldId") if fx.hasElement("fieldId") else None
        if fid is not None and fid != field_name:
            continue
        if fx.hasElement("errorInfo"):
            ei = fx.getElement("errorInfo")
            if ei.hasElement("message"):
                return ei.getElementAsString("message")
        return "fieldException"
    return None


def _classify_live_ticker(sd, field_name: str) -> dict:
    """One ticker's per-security element (live/ReferenceDataRequest shape) ->
    {"value", "status", "detail"}. status is OK / SECURITY_ERROR / FIELD_EXCEPTION /
    NO_VALUE (2026-09-18, added so a live pull can tell a rejected ticker apart from a
    field simply not populated -- feeds assess_ticker_assumptions / record_vol_ticker_
    checks below, itself never a new Bloomberg request: same response, read more fully)."""
    sec_err = _bbg_error_message(sd, "securityError")
    if sec_err:
        return {"value": None, "status": "SECURITY_ERROR", "detail": sec_err}
    value = None
    if sd.hasElement("fieldData"):
        fd = sd.getElement("fieldData")
        if fd.hasElement(field_name):
            value = fd.getElement(field_name).getValue()
    if value is not None:
        return {"value": value, "status": "OK", "detail": ""}
    field_err = _bbg_field_exception_message(sd, field_name)
    if field_err:
        return {"value": None, "status": "FIELD_EXCEPTION", "detail": field_err}
    return {"value": None, "status": "NO_VALUE", "detail": "no value returned for this ticker"}


def _classify_hist_ticker(sec_data, field_name: str) -> dict:
    """Same as `_classify_live_ticker` but for the HistoricalDataRequest shape, where
    fieldData is a list of daily points (see `_fetch`'s historical branch)."""
    sec_err = _bbg_error_message(sec_data, "securityError")
    if sec_err:
        return {"value": None, "status": "SECURITY_ERROR", "detail": sec_err}
    value = None
    if sec_data.hasElement("fieldData"):
        fd = sec_data.getElement("fieldData")
        if fd.numValues() > 0:
            point = fd.getValueAsElement(0)
            if point.hasElement(field_name):
                value = point.getElement(field_name).getValue()
    if value is not None:
        return {"value": value, "status": "OK", "detail": ""}
    field_err = _bbg_field_exception_message(sec_data, field_name)
    if field_err:
        return {"value": None, "status": "FIELD_EXCEPTION", "detail": field_err}
    return {"value": None, "status": "NO_VALUE", "detail": "no value returned for this ticker"}


class VolBloombergSource:
    """Live blpapi wrapper for FX vol quotes. Opens its OWN blpapi.Session -- see module
    docstring. Every ticker/field name used here is UNVERIFIED (see module docstring
    numbered list); a per-ticker failure is recorded in the returned VolFetchResult's
    diagnostics, never raised -- only session/service-open failures raise
    MarketDataError (there is genuinely nothing useful to return in that case)."""

    def __init__(self, host: str = "localhost", port: int = 8194, timeout_ms: int = 30000) -> None:
        try:
            import blpapi  # noqa: F401
        except ImportError as exc:
            raise MarketDataError(
                "blpapi not installed. Install the Bloomberg Python API on the terminal "
                "machine (see WAPI <GO>), or use VolFileSource with a saved snapshot."
            ) from exc

        self._blpapi = blpapi
        self.host = host
        self.port = port
        self.timeout_ms = timeout_ms

        session_options = blpapi.SessionOptions()
        session_options.setServerHost(host)
        session_options.setServerPort(port)
        self._session = blpapi.Session(session_options)

        if not self._session.start():
            raise MarketDataError(
                f"Cannot connect to Bloomberg on {host}:{port}. Is the Terminal running and logged in?"
            )
        if not self._session.openService("//blp/refdata"):
            raise MarketDataError(
                f"Cannot connect to Bloomberg on {host}:{port}. Is the Terminal running and logged in?"
            )
        self._service = self._session.getService("//blp/refdata")

    def name(self) -> str:
        return f"VolBloombergSource({self.host}:{self.port})"

    def close(self) -> None:
        try:
            self._session.stop()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "VolBloombergSource":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- internal: request/response, named-element access (see pull_marks.py /
    # rates_marketdata.py for the pattern this matches) -----------------------------------

    def _fetch(self, tickers: Sequence[str], as_of: Optional[date]):
        """Returns (info: {ticker: {"value", "status", "detail"}}, batch_detail:
        Optional[str]). status is OK / SECURITY_ERROR / FIELD_EXCEPTION / NO_VALUE per
        ticker (2026-09-18, added so a live pull can tell a rejected ticker shape apart
        from a field simply not populated yet -- feeds assess_ticker_assumptions /
        record_vol_ticker_checks below; this is reading more of the SAME response, not an
        extra Bloomberg request). `batch_detail` is set (info may be partial/empty) on a
        timeout or any unexpected exception talking to the fake/real session -- this
        method NEVER raises, so a single bad batch degrades to "everything in it missing"
        with a reason attached, rather than aborting the whole pull (see module
        docstring)."""
        blpapi = self._blpapi
        try:
            live = as_of is None
            request_type = "ReferenceDataRequest" if live else "HistoricalDataRequest"
            request = self._service.createRequest(request_type)
            for t in tickers:
                request.getElement("securities").appendValue(t)
            request.getElement("fields").appendValue(VOL_FIELD)
            if not live:
                d = as_of.strftime("%Y%m%d")
                request.set("startDate", d)
                request.set("endDate", d)

            self._session.sendRequest(request)

            out: Dict[str, dict] = {}
            consecutive_timeouts = 0
            while True:
                event = self._session.nextEvent(self.timeout_ms)
                event_type = event.eventType()
                if event_type == blpapi.Event.TIMEOUT:
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
                        return out, f"nextEvent timed out after {consecutive_timeouts} consecutive waits"
                    continue
                consecutive_timeouts = 0
                event_is_ours = False
                for msg in event:
                    if not msg.hasElement("securityData"):
                        continue
                    event_is_ours = True
                    sec_data = msg.getElement("securityData")
                    if live:
                        for i in range(sec_data.numValues()):
                            sd = sec_data.getValueAsElement(i)
                            ticker = sd.getElementAsString("security")
                            out[ticker] = _classify_live_ticker(sd, VOL_FIELD)
                    else:
                        ticker = sec_data.getElementAsString("security")
                        out[ticker] = _classify_hist_ticker(sec_data, VOL_FIELD)
                if event_type == blpapi.Event.RESPONSE and event_is_ours:
                    break
            return out, None
        except Exception as exc:  # noqa: BLE001 -- never let a single batch crash the pull
            return {}, f"{type(exc).__name__}: {exc}"

    def get_vol_quotes(
        self, pairs: Sequence[str], as_of: Optional[date] = None,
        tenors: Optional[Sequence[str]] = None,
    ) -> VolFetchResult:
        """Live ReferenceDataRequest (as_of=None) or HistoricalDataRequest (as_of given)
        for every (pair, tenor, quote_type) ticker, batched into one request. Per-ticker
        failures are recorded in the result's `diagnostics`, never raised -- see class
        docstring. `tenors` narrows the tenor grid (used by --probe to fetch just one
        tenor); defaults to VOL_TENORS."""
        from data.bloomberg.live import book_today
        effective_as_of = as_of if as_of is not None else book_today()
        tenor_list = list(tenors) if tenors is not None else list(VOL_TENORS)

        needed = [
            (normalize_pair(pair), tenor, quote_type, vol_ticker(pair, tenor, quote_type))
            for pair in pairs for tenor in tenor_list for quote_type in VOL_QUOTE_TYPES
        ]
        all_tickers = sorted({n[3] for n in needed})
        values, batch_detail = self._fetch(all_tickers, as_of) if all_tickers else ({}, None)

        diagnostics: List[dict] = []
        pairs_out: Dict[str, PairVolSnapshot] = {}
        for pair, tenor, quote_type, ticker in needed:
            info = values.get(ticker)
            if info is None:
                diagnostics.append({
                    "pair": pair, "tenor": tenor, "quote_type": quote_type, "ticker": ticker,
                    "status": "MISSING", "bbg_status": "NO_VALUE",
                    "detail": batch_detail or "no value returned for this ticker",
                })
                continue
            val = info.get("value")
            if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
                diagnostics.append({
                    "pair": pair, "tenor": tenor, "quote_type": quote_type, "ticker": ticker,
                    "status": "MISSING", "bbg_status": info.get("status", "NO_VALUE"),
                    "detail": info.get("detail") or batch_detail or "no value returned for this ticker",
                })
                continue
            pairs_out.setdefault(pair, PairVolSnapshot(pair=pair, as_of=effective_as_of, quotes=[]))
            pairs_out[pair].quotes.append(VolQuote(
                tenor=tenor, quote_type=quote_type, ticker=ticker, value=float(val),
                field=VOL_FIELD, source="BBG",
            ))

        return VolFetchResult(
            as_of=effective_as_of, source="BBG_BDP" if as_of is None else "BBG_BDH",
            pairs=pairs_out, diagnostics=diagnostics,
        )


# --------------------------------------------------------------------------- vol_quotes staging table

VOL_QUOTES_COLUMNS = ["as_of_date", "pair", "tenor", "quote_type", "value", "ticker", "field", "source", "snapped_at"]

_VOL_QUOTES_DDL = """
CREATE TABLE IF NOT EXISTS vol_quotes (
  as_of_date  TEXT NOT NULL,
  pair        TEXT NOT NULL,
  tenor       TEXT NOT NULL,
  quote_type  TEXT NOT NULL,
  value       REAL NOT NULL,
  ticker      TEXT NOT NULL,
  field       TEXT NOT NULL,
  source      TEXT NOT NULL,
  snapped_at  TEXT NOT NULL,
  PRIMARY KEY (as_of_date, pair, tenor, quote_type, source)
);
"""


def ensure_vol_quotes_table(conn: sqlite3.Connection) -> None:
    conn.execute(_VOL_QUOTES_DDL)


def write_vol_quotes(
    conn: sqlite3.Connection,
    snapshot: Union[VolFetchResult, PairVolSnapshot, Dict[str, PairVolSnapshot]],
    as_of_date: str,
    source: str = "BBG_BDP",
) -> int:
    """Insert one row per quote into vol_quotes (creating the table defensively if it
    doesn't exist -- see module docstring). Accepts a VolFetchResult (the normal case,
    from either source's get_vol_quotes), a single PairVolSnapshot, or a plain
    {pair: PairVolSnapshot} dict.

    Uses INSERT OR REPLACE keyed on (as_of_date, pair, tenor, quote_type, source), so
    re-running a pull for the same day/source updates rather than duplicate-key errors.
    Returns the number of rows written.
    """
    ensure_vol_quotes_table(conn)
    if isinstance(snapshot, VolFetchResult):
        pairs = snapshot.pairs
    elif isinstance(snapshot, PairVolSnapshot):
        pairs = {snapshot.pair: snapshot}
    elif isinstance(snapshot, dict):
        pairs = snapshot
    else:
        raise TypeError(f"Unsupported snapshot type for write_vol_quotes: {type(snapshot)!r}")

    stamp = snapped_at(date.fromisoformat(as_of_date))
    rows = [
        (as_of_date, pair, q.tenor, q.quote_type, float(q.value), q.ticker, q.field, source, stamp)
        for pair, pv in pairs.items() for q in pv.quotes
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO vol_quotes "
            "(as_of_date, pair, tenor, quote_type, value, ticker, field, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


# --------------------------------------------------------------------------- assumption verification (2026-09-18)
# tools/bbg_diagnostics.py::check_unverified_assumptions used to just count how many
# UNVERIFIED lines remain in this module's docstring and unconditionally tell the user to
# run --probe by hand. data/bloomberg/live.py's `_vol_step` already requests every one of
# these tickers on every live pull with an open FX_OPTION in the book (2026-09-17) -- so
# the live pull itself is the probe. This section turns what Bloomberg actually returned
# (VolFetchResult.diagnostics, already produced by get_vol_quotes -- no new request) into
# a persistent `vol_ticker_checks` table row per assumption, so the diagnostics check can
# report a real PASS/FAIL instead.

ASSUMPTION_DESCRIPTIONS: Dict[str, str] = {
    "atm_ticker": "ATM ticker shape '<PAIR>V<TENOR> BGN Curncy' (probe item 1)",
    "rr25_ticker": "25-delta risk reversal ticker '<PAIR>25R<TENOR> BGN Curncy' (probe item 2)",
    "bf25_ticker": "25-delta butterfly ticker '<PAIR>25B<TENOR> BGN Curncy' (probe item 3)",
    "rr10_ticker": "10-delta risk reversal ticker '<PAIR>10R<TENOR> BGN Curncy' (probe item 4)",
    "bf10_ticker": "10-delta butterfly ticker '<PAIR>10B<TENOR> BGN Curncy' (probe item 5)",
    "px_last_field": "field PX_LAST is correct for all five quote types (probe item 6)",
    "on_tenor": "'ON' (overnight) is a valid tenor suffix on these vol tickers (probe item 7)",
    "request_type": "live ReferenceDataRequest (as_of=None) is the right request shape for vol quotes (probe item 8)",
    "vol_scale": "values come back already in vol points (7.85 = 7.85%), no separate scale field (probe item 9)",
}

# Fixed order matches the module docstring's numbered list (1-9).
ALL_ASSUMPTION_IDS: List[str] = list(ASSUMPTION_DESCRIPTIONS)

_QUOTE_TYPE_ASSUMPTION: Dict[str, str] = {
    "ATM": "atm_ticker", "RR25": "rr25_ticker", "BF25": "bf25_ticker",
    "RR10": "rr10_ticker", "BF10": "bf10_ticker",
}

# Priority when several diagnostics exist for the same assumption this cycle -- a rejected
# ticker/field is more informative than a plain "no value" (same relative order
# pull_marks.py's _classify_secs uses for a whole batch, applied per-assumption here).
_OUTCOME_PRIORITY = {"SECURITY_ERROR": 0, "FIELD_EXCEPTION": 1, "NO_VALUE": 2}


def _assumption_row(assumption_id: str, tickers: Sequence[str], outcome: str,
                     value: Optional[float], detail: str) -> dict:
    return {
        "assumption_id": assumption_id, "description": ASSUMPTION_DESCRIPTIONS[assumption_id],
        "tickers": ", ".join(tickers), "outcome": outcome,
        "value": None if value is None else float(value), "detail": detail,
    }


def _pick_assumption_outcome(assumption_id: str, ok_quotes: Sequence[VolQuote],
                              bad_diagnostics: Sequence[dict]) -> Optional[dict]:
    """One assumption's verdict from whatever evidence this cycle's pull produced for it:
    a confirmed value beats a failure (the ticker shape works even if a different
    tenor/pair happened to fail for an unrelated reason this cycle), and among failures
    the most specific rejection wins (SECURITY_ERROR names a wrong ticker; FIELD_EXCEPTION
    names a wrong field on an otherwise-valid ticker; NO_VALUE is the least informative).
    None if this assumption was not exercised at all this cycle (e.g. no option in the
    book, so nothing was requested) -- record_vol_ticker_checks then leaves any
    previously recorded row for it untouched."""
    if ok_quotes:
        q = ok_quotes[0]
        return _assumption_row(assumption_id, [q.ticker], "OK", q.value,
                                f"Bloomberg returned {q.value} for {q.ticker}.")
    if bad_diagnostics:
        d = sorted(bad_diagnostics, key=lambda x: _OUTCOME_PRIORITY.get(x.get("bbg_status", ""), 3))[0]
        outcome = d.get("bbg_status") or "NO_VALUE"
        return _assumption_row(assumption_id, [d["ticker"]], outcome, None,
                                f"Bloomberg returned {outcome} for {d['ticker']}: {d.get('detail', '')}")
    return None


def _scale_outcome(value: float) -> Tuple[str, str]:
    """Judge whether `value` looks like Bloomberg's vol-points convention (7.85 = 7.85%)
    or a decimal fraction (0.0785) that this module would otherwise store un-rescaled --
    probe item 9, judged from the value itself per the task spec, no extra Bloomberg
    request. Checked on an ATM value only (RR/BF quotes can legitimately be small in
    either convention, e.g. near a flat skew, so they are not a reliable signal)."""
    magnitude = abs(value)
    if 0.5 <= magnitude <= 200:
        return "OK", f"value {value:g} is a plausible vol-points quote (e.g. 7.85 meaning 7.85%)."
    if 0 < magnitude < 0.5:
        return "OUT_OF_RANGE", (f"value {value:g} looks like a decimal fraction (e.g. 0.075), not vol points "
                                 "(7.5) -- vol_quotes.value may need to be re-scaled, or PX_LAST is not the "
                                 "right field/convention here.")
    return "OUT_OF_RANGE", f"value {value:g} is outside the plausible vol-points range (0.5 to 200)."


def assess_ticker_assumptions(result: VolFetchResult) -> List[dict]:
    """Turn one live VolFetchResult (VolBloombergSource.get_vol_quotes's return value)
    into a verification record per UNVERIFIED assumption in this module's docstring: a
    dict per assumption id with `tickers` / `outcome` / `value` / `detail`, ready for
    record_vol_ticker_checks to persist. Only assumptions with real evidence THIS CYCLE
    are returned -- one never exercised (e.g. an 'ON' tenor request that never went out
    because there were no open options at all) is simply absent from the result, rather
    than a manufactured "not checked" overwriting a real prior confirmation.

    A VolFileSource result (offline/test mode) has pair-level-only diagnostics with no
    `ticker` key (see that class's docstring) -- `failures` below is filtered to
    ticker-keyed entries, so those are never mistaken for a rejection; successes (VolQuote
    objects, which always carry ticker/tenor/quote_type/value regardless of source) still
    count as evidence either way."""
    successes = [(pair, q) for pair, pv in result.pairs.items() for q in pv.quotes]
    failures = [d for d in result.diagnostics if d.get("ticker")]

    out: List[dict] = []
    for quote_type, assumption_id in _QUOTE_TYPE_ASSUMPTION.items():
        ok = [q for _, q in successes if q.quote_type == quote_type]
        bad = [d for d in failures if d.get("quote_type") == quote_type]
        row = _pick_assumption_outcome(assumption_id, ok, bad)
        if row:
            out.append(row)

    field_bad = [d for d in failures if d.get("bbg_status") == "FIELD_EXCEPTION"]
    if field_bad:
        d = field_bad[0]
        out.append(_assumption_row("px_last_field", [d["ticker"]], "FIELD_EXCEPTION", None,
                                    f"Bloomberg rejected field PX_LAST on {d['ticker']}: {d.get('detail', '')}"))
    elif successes:
        _, q = successes[0]
        out.append(_assumption_row("px_last_field", [q.ticker], "OK", q.value,
                                    f"PX_LAST returned a value on {q.ticker} ({q.value})."))

    on_ok = [q for _, q in successes if q.tenor == "ON"]
    on_bad = [d for d in failures if d.get("tenor") == "ON"]
    row = _pick_assumption_outcome("on_tenor", on_ok, on_bad)
    if row:
        out.append(row)

    if successes or failures:
        example = successes[0][1].ticker if successes else failures[0]["ticker"]
        out.append(_assumption_row("request_type", [example], "OK", None,
                                    "Live ReferenceDataRequest returned a structured per-security response "
                                    "(a wrong request shape fails the whole batch with no per-ticker detail "
                                    "at all, never gets this far)."))

    atm_ok = [q for _, q in successes if q.quote_type == "ATM"]
    if atm_ok:
        q = atm_ok[0]
        outcome, detail = _scale_outcome(q.value)
        out.append(_assumption_row("vol_scale", [q.ticker], outcome, q.value, detail))

    return out


_VOL_TICKER_CHECKS_DDL = """
CREATE TABLE IF NOT EXISTS vol_ticker_checks (
  assumption_id TEXT PRIMARY KEY,
  description   TEXT NOT NULL,
  last_checked  TEXT NOT NULL,
  tickers       TEXT NOT NULL,
  outcome       TEXT NOT NULL,
  value         REAL,
  detail        TEXT NOT NULL
);
"""


def ensure_vol_ticker_checks_table(conn: sqlite3.Connection) -> None:
    conn.execute(_VOL_TICKER_CHECKS_DDL)


def record_vol_ticker_checks(conn: sqlite3.Connection, result: VolFetchResult,
                              checked_at: Optional[str] = None) -> int:
    """Persist assess_ticker_assumptions(result) into vol_ticker_checks (created
    defensively), one row per assumption id, INSERT OR REPLACE so the latest live pull's
    verdict always wins over a stale one. Returns the number of assumption rows written
    (0 when nothing in `result` bears on any assumption -- e.g. every ticker timed out
    with no per-security breakdown at all)."""
    ensure_vol_ticker_checks_table(conn)
    rows = assess_ticker_assumptions(result)
    if not rows:
        return 0
    checked_at = checked_at or datetime.now(timezone.utc).isoformat()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO vol_ticker_checks "
            "(assumption_id, description, last_checked, tickers, outcome, value, detail) "
            "VALUES (?,?,?,?,?,?,?)",
            [(r["assumption_id"], r["description"], checked_at, r["tickers"], r["outcome"], r["value"], r["detail"])
             for r in rows],
        )
    return len(rows)


def read_vol_ticker_checks(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{assumption_id: {description, last_checked, tickers, outcome, value, detail}} --
    empty dict if the table doesn't exist yet or has no rows (no live pull with options
    in the book has ever run). Never raises."""
    try:
        ensure_vol_ticker_checks_table(conn)
        rows = conn.execute(
            "SELECT assumption_id, description, last_checked, tickers, outcome, value, detail "
            "FROM vol_ticker_checks").fetchall()
    except sqlite3.Error:
        return {}
    return {r[0]: {"description": r[1], "last_checked": r[2], "tickers": r[3],
                   "outcome": r[4], "value": r[5], "detail": r[6]} for r in rows}


# --------------------------------------------------------------------------- read helpers (for options-pricer)

def _pick_source(sources: set) -> str:
    """Never blend sources for one smile -- same rule as engine/rates/store.py's
    _read_curve_quotes: prefer BBG_BDP (the live pull's default source), else BBG_BDH
    (the historical-pull source), else whichever source sorts first alphabetically, for
    determinism."""
    if "BBG_BDP" in sources:
        return "BBG_BDP"
    if "BBG_BDH" in sources:
        return "BBG_BDH"
    return sorted(sources)[0]


def vol_smile(conn: sqlite3.Connection, as_of: str, pair: str) -> Dict[str, Dict[str, float]]:
    """{tenor: {quote_type: value}} for (as_of, pair) from vol_quotes, using a single
    preferred source (see _pick_source) -- never blends sources. Empty dict if nothing
    is staged for this (as_of, pair)."""
    ensure_vol_quotes_table(conn)
    rows = conn.execute(
        "SELECT tenor, quote_type, value, source FROM vol_quotes WHERE as_of_date = ? AND pair = ?",
        (as_of, normalize_pair(pair)),
    ).fetchall()
    if not rows:
        return {}
    source = _pick_source({r[3] for r in rows})
    smile: Dict[str, Dict[str, float]] = {}
    for tenor, quote_type, value, src in rows:
        if src != source:
            continue
        smile.setdefault(tenor, {})[quote_type] = value
    return smile


def atm_vol_for_expiry(conn: sqlite3.Connection, as_of: str, pair: str, expiry_date: str) -> Optional[float]:
    """Linear interpolation of ATM vol, in variance-time, between the two tenor nodes
    bracketing `expiry_date` (var = vol^2 * t, t approximated in calendar days via
    tenor_to_days -- for sorting/interpolation only, not a real accrual/day-count
    convention, same caveat as rates_marketdata.tenor_to_days). Returns None if fewer
    than 2 ATM nodes are staged, or if `expiry_date` falls outside the tenor range --
    this NEVER extrapolates (CLAUDE.md-style "missing stays missing", per the task's
    "refuse to extrapolate" instruction).

    Delta-smile construction (turning RR/BF into a full smile) is deliberately NOT done
    here -- options-pricer feeds the raw smile from vol_smile() to the vendored
    FXDeltaVolSurface itself; this function only interpolates the ATM term structure.
    """
    smile = vol_smile(conn, as_of, pair)
    nodes = []
    for tenor, quotes in smile.items():
        if "ATM" not in quotes:
            continue
        try:
            days = tenor_to_days(tenor)
        except ValueError:
            continue
        nodes.append((days, quotes["ATM"]))
    if len(nodes) < 2:
        return None
    nodes.sort(key=lambda n: n[0])

    target_days = (date.fromisoformat(expiry_date) - date.fromisoformat(as_of)).days
    if target_days < nodes[0][0] or target_days > nodes[-1][0]:
        return None
    for d, v in nodes:
        if d == target_days:
            return v
    for (d0, v0), (d1, v1) in zip(nodes, nodes[1:]):
        if d0 <= target_days <= d1:
            if d1 == d0:
                return v0
            var0 = (v0 ** 2) * d0
            var1 = (v1 ** 2) * d1
            frac = (target_days - d0) / (d1 - d0)
            var_t = var0 + frac * (var1 - var0)
            return (var_t / target_days) ** 0.5
    return None  # pragma: no cover (unreachable given the bounds check above)


# --------------------------------------------------------------------------- CLI

def _write_diag(diag: dict, path: Path) -> None:
    """Never let a diagnostics-writing failure mask the run's real exit code -- same
    discipline as pull_marks.py's write_diagnostics."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(diag, f, indent=2, default=str)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: failed to write diagnostics to {path}: {type(e).__name__}: {e}", file=sys.stderr)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, help="SQLite DB path (':memory:' is accepted but pointless for a CLI run)")
    parser.add_argument("--as-of", help="as_of date, ISO YYYY-MM-DD (required for a normal pull; optional for --probe)")
    parser.add_argument("--file", help="path to a saved vol snapshot JSON (VolFileSource, offline mode); omit for the live VolBloombergSource")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--probe", action="store_true",
                         help="probe mode: request ONE ticker per quote_type for EURUSD 1M and print what came "
                              "back (or the failure) -- run this on the Bloomberg terminal to check the ticker/"
                              "field guesses in the module docstring. Writes <db>.diag.json; no vol_quotes rows "
                              "written.")
    return parser


def _open_source(args) -> Union[VolBloombergSource, VolFileSource]:
    if args.file:
        return VolFileSource(args.file)
    return VolBloombergSource(args.host, args.port)


def _run_probe(args, diag: dict) -> int:
    pair, tenor = "EURUSD", "1M"
    diag["probe"] = {"pair": pair, "tenor": tenor}
    as_of_date = date.fromisoformat(args.as_of) if args.as_of else None

    try:
        source = _open_source(args)
    except MarketDataError as e:
        diag["summary"] = {"outcome": "FAILED", "exit_code": 4, "error": str(e)}
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    try:
        result = source.get_vol_quotes([pair], as_of=as_of_date, tenors=[tenor])
    finally:
        if hasattr(source, "close"):
            source.close()

    steps = []
    pv = result.pairs.get(pair)
    # Filter to `tenor` explicitly: VolFileSource.get_vol_quotes ignores `tenors` (a file
    # snapshot already contains whichever tenors it has -- see its docstring), so
    # pv.quotes may include every tenor for the pair, not just the one being probed.
    by_quote_type = {q.quote_type: q.value for q in (pv.quotes if pv else []) if q.tenor == tenor}
    for quote_type in VOL_QUOTE_TYPES:
        ticker = vol_ticker(pair, tenor, quote_type)
        value = by_quote_type.get(quote_type)
        outcome = "OK" if value is not None else "MISSING"
        step_diag = [d for d in result.diagnostics if d.get("quote_type") == quote_type]
        steps.append({"quote_type": quote_type, "ticker": ticker, "outcome": outcome,
                       "value": value, "diagnostics": step_diag})
        print(f"PROBE {quote_type:>5} {ticker}: {outcome} value={value}", file=sys.stderr)

    failures = [s for s in steps if s["outcome"] != "OK"]
    diag["probe"]["steps"] = steps
    diag["summary"] = {
        "outcome": "OK" if not failures else "PROBE_COMPLETE_WITH_FAILURES",
        "exit_code": 0, "failures": len(failures),
    }
    return 0


def _run_pull(args, diag: dict) -> int:
    if not args.as_of:
        diag["summary"] = {"outcome": "FAILED", "exit_code": 2, "error": "--as-of is required unless --probe"}
        print("ERROR: --as-of is required unless --probe", file=sys.stderr)
        return 2
    try:
        as_of_date = date.fromisoformat(args.as_of)
    except ValueError as e:
        diag["summary"] = {"outcome": "FAILED", "exit_code": 2, "error": f"bad --as-of: {e}"}
        print(f"ERROR: --as-of {args.as_of!r} is not a valid ISO date (YYYY-MM-DD): {e}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    try:
        pairs = vol_pairs_needed(conn)
        diag["pairs"] = pairs

        try:
            source = _open_source(args)
        except MarketDataError as e:
            diag["summary"] = {"outcome": "FAILED", "exit_code": 4, "error": str(e)}
            print(f"ERROR: {e}", file=sys.stderr)
            return 4

        try:
            result = source.get_vol_quotes(pairs, as_of=as_of_date)
        finally:
            if hasattr(source, "close"):
                source.close()

        diag["fetch_diagnostics"] = result.diagnostics
        write_source = result.source
        n = write_vol_quotes(conn, result, as_of_date=args.as_of, source=write_source)
        conn.commit()

        requested = len(pairs) * len(VOL_TENORS) * len(VOL_QUOTE_TYPES)
        written = sum(len(pv.quotes) for pv in result.pairs.values())
        partial = written < requested
        diag["summary"] = {
            "outcome": "OK" if not partial else "PARTIAL",
            "exit_code": 0 if not partial else 5,
            "requested": requested, "written": written, "rows_written_to_db": n,
            "source": write_source,
        }
        if partial:
            print(f"ERROR: only {written}/{requested} requested vol quotes obtained; "
                  f"{n} row(s) written to {args.db} -- see fetch_diagnostics in the diag JSON", file=sys.stderr)
        print(f"wrote {n} vol_quotes row(s) to {args.db} ({written}/{requested} requested quotes obtained)",
              file=sys.stderr)
        return 0 if not partial else 5
    finally:
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: ``py -m data.bloomberg.vol_marketdata``.

    Exit codes (mirrors pull_marks.py's table):
      0  OK -- pull: every requested (pair, tenor, quote_type) quote was written;
         probe: the source opened and every step ran (individual step outcomes are
         exploratory, not pass/fail for the probe run itself -- see summary.failures).
      2  bad --as-of (missing, for a normal pull, or not a valid ISO date).
      3  unhandled exception.
      4  source open failure (blpapi not installed, session/service open failed, or
         VolFileSource given a bad path).
      5  partial pull -- one or more requested (pair, tenor, quote_type) keys were not
         written (see fetch_diagnostics in the diag JSON for why, per ticker).

    Every run (pull or probe) always writes a diagnostics file at "<db>.diag.json"
    (written in a finally block, even on an unhandled exception).
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    diag: dict = {
        "mode": "probe" if args.probe else "pull",
        "db": args.db, "file": args.file, "as_of": args.as_of,
        "machine_time_utc": datetime.now(timezone.utc).isoformat(),
    }
    diag_path = Path(str(args.db) + ".diag.json")

    exit_code = 1
    try:
        if args.probe:
            exit_code = _run_probe(args, diag)
        else:
            exit_code = _run_pull(args, diag)
    except Exception as e:  # noqa: BLE001 -- never crash without writing the diag file
        diag["exception"] = {"type": type(e).__name__, "message": str(e), "traceback": traceback.format_exc()}
        diag["summary"] = {"outcome": "FAILED", "exit_code": 3}
        print(f"ERROR: unhandled exception: {type(e).__name__}: {e}", file=sys.stderr)
        exit_code = 3
    finally:
        _write_diag(diag, diag_path)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
