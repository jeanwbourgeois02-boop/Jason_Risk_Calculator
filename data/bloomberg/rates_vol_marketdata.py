"""Bloomberg interest-rate option volatility feed -- swaption/cap ATM vol grid, staged
into a new ``rate_vol_quotes`` table for ``engine/rates_vol`` (swaptions, caps/floors,
SABR and Bermudan pricing, options merge Phase 6) to copy from.

Structure mirrors ``data/bloomberg/vol_marketdata.py`` (the FX vol feed) and
``data/bloomberg/rates_marketdata.py`` (the OIS curve feed) exactly: two source
implementations behind one informal interface (``get_rate_vol_quotes``):
  - ``RateVolBloombergSource``: live blpapi wrapper. Opens its OWN blpapi.Session (same
    deliberate non-sharing as the other two market-data modules in this directory --
    tracked as a follow-up by the housekeeper, not addressed here). Requires the
    `blpapi` package (raises MarketDataError with an install hint if missing) and a
    running Bloomberg Terminal / B-PIPE. NEVER crashes on a per-ticker failure: every
    missing ticker is recorded in ``RateVolFetchResult.diagnostics`` instead.
  - ``RateVolFileSource``: reads a saved JSON snapshot (see
    data/bloomberg/fixtures/rate_vol_snapshot_v1.json, a labelled-synthetic fixture --
    no live feed was available to sample from) -- no blpapi dependency at all, used by
    tests and offline work.

Bloomberg is NOT reachable from this development machine. Every ticker/field name and
request-shape choice below is UNVERIFIED. The user will run ``--probe`` on the
Bloomberg terminal to check these; tick off as each is confirmed (mirrors
vol_marketdata.py's numbered list and docs/open-questions.md's pull_marks.py items):

  1. UNVERIFIED -- currency -> two-letter vol-ticker prefix (USD='US', EUR='EU',
     GBP='BP', JPY='JY', CHF='SF', CAD='CD', AUD='AD'), guessed by analogy with the
     first letters of each currency's OIS ticker root in
     ``rates_marketdata.OIS_CURVES`` (USOSFR, EESWE, BPSWS, JYSO, SFSNT, CDSO, ADSO) --
     note EUR's OIS root uses 'EE', but the task spec's own worked example uses 'EU'
     for EUR vol tickers, so 'EU' (not 'EE') is used here; this divergence itself is
     UNVERIFIED.
  2. UNVERIFIED -- swaption ATM lognormal ticker
     '<prefix>SV<expiry_code><tenor_code> Curncy', e.g. 'USSV0110 Curncy' read as
     1Y-expiry x 10Y-tenor (see item 5 for the code scheme). This is this module's best
     reading of the task's own worked example, not an independently verified
     Bloomberg convention.
  3. UNVERIFIED -- swaption ATM normal (bp) ticker, same shape with 'SN' in place of
     'SV', e.g. 'USSN0110 Curncy'.
  4. UNVERIFIED -- cap/floor vol ticker '<prefix>CV<expiry_code> Curncy' (lognormal) /
     '<prefix>CN<expiry_code> Curncy' (normal) -- caps have no underlying-tenor axis
     (the strip's tenor is the cap's own maturity), so the ticker carries only an
     expiry code, unlike the swaption ticker's two codes.
  5. UNVERIFIED -- expiry code scheme: sub-year expiries (1M/3M/6M) use a single
     letter borrowed from ``rates_marketdata.py``'s OIS depo-tenor lettering (A=1M,
     C=3M, F=6M); expiries >= 1Y use a zero-padded two-digit year number (1Y='01',
     2Y='02', 5Y='05', 10Y='10'). Underlying tenors (always >= 1Y in this module's
     scope) use the same two-digit year-number scheme (1Y='01' ... 30Y='30'). Never
     independently confirmed against a live terminal.
  6. UNVERIFIED -- field PX_LAST is correct for all four ticker families (vs a
     bid/ask/mid-specific field, or a BGN-composite-specific suffix the way FX vol
     tickers carry one).
  7. UNVERIFIED -- whether a live ReferenceDataRequest (as_of=None) vs a
     HistoricalDataRequest (as_of given) is the right split here, mirroring the same
     open question in vol_marketdata.py / rates_marketdata.py.
  8. UNVERIFIED -- values come back already in the units documented below with no
     separate scale field to read.
  9. UNVERIFIED -- strike-offset ("skew") tickers for ``quote_type='SKEW'`` (e.g. ATM
     +/-25bp/50bp/100bp strike points) are NOT mapped by this module at all yet --
     ``rate_vol_ticker`` only builds ATM tickers. The ``rate_vol_quotes`` schema
     reserves ``quote_type``/``strike_offset_bp`` columns for this so no migration is
     needed once the skew ticker convention is confirmed and added.

Units (store RAW, exactly as Bloomberg quotes them -- same "store raw, scale
downstream" discipline as vol_marketdata.py's vol_quotes and rates_marketdata.py's
pre-scaled curve_quotes; see each column's meaning below):
  - LOGNORMAL (Black) vols are stored in **percent** (e.g. 18.50 meaning 18.5%),
    matching ``engine/rates_vol/inputs.py``'s ``rate_vols.vol`` convention... except
    that consumer stores a plain decimal-or-percent number it never scales either (see
    its module docstring); this module stores the raw Bloomberg print, unconverted.
  - NORMAL (bp-vol) vols are stored in **basis points** (e.g. 95.0 meaning 95bp), the
    standard Bloomberg convention for USSN/EUSN-style tickers.
  ``engine/rates_vol/inputs.py`` REJECTS a NORMAL-quoted flat vol outright (its vendored
  pricer is lognormal Black-76 only) -- this module stages NORMAL quotes anyway (for
  completeness / a future normal-vol pricer) but nothing downstream currently consumes
  them; see that module's docstring.

Known schema quirk (as specified by the task, not something this module works around):
``rate_vol_quotes``'s primary key does NOT include ``vol_type`` -- it mirrors
``engine/rates_vol/inputs.py``'s own ``rate_vols`` table, whose primary key has the
exact same shape (``strike_or_ATM`` in place of ``strike_offset_bp``, also no
``vol_type``). Concretely: writing a LOGNORMAL and a NORMAL quote for the same
(as_of_date, ccy, index, instrument, expiry_tenor, underlying_tenor, strike_offset_bp,
source) collide on the same primary key, and INSERT OR REPLACE means whichever is
written last wins -- the earlier vol_type's row is silently gone. ``write_rate_vol_quotes``
does not reorder or dedupe to work around this (same "last write wins, no magic"
discipline as the rest of this module); a caller staging both vol types for the same
node under one ``source`` label must give them different ``source`` values to keep both
rows, or accept that only one survives. Flagged here rather than silently patched, per
this task's instructions -- an actual PK change is out of this module's scope (it would
need to be requested against ``engine/rates_vol/inputs.py``'s matching table too, which
this agent does not own).

Writes go to the ``rate_vol_quotes`` staging table, created defensively with
CREATE TABLE IF NOT EXISTS (see ``ensure_rate_vol_quotes_table`` / ``write_rate_vol_quotes``).

CLI (see ``main`` docstring for full usage and exit codes):
    py -m data.bloomberg.rates_vol_marketdata --db risk.db --as-of 2026-09-17 --file data/bloomberg/fixtures/rate_vol_snapshot_v1.json
    py -m data.bloomberg.rates_vol_marketdata --db risk.db --as-of 2026-09-17          # live (needs blpapi + terminal)
    py -m data.bloomberg.rates_vol_marketdata --db risk.db --probe                     # ticker/field probe, run on the Bloomberg terminal
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

from data.bloomberg.rates_marketdata import OIS_INDEX
from engine.rates.store import snapped_at

# --------------------------------------------------------------------------- scope constants

EXPIRY_TENORS = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y"]
UNDERLYING_TENORS = ["1Y", "2Y", "5Y", "10Y", "30Y"]
INSTRUMENTS = ["SWAPTION", "CAP"]
VOL_TYPES = ["LOGNORMAL", "NORMAL"]
QUOTE_TYPES = ["ATM", "SKEW"]  # only ATM is fetched/built by this module today -- see item 9
FIELD = "PX_LAST"

# ccy -> two-letter vol-ticker prefix (UNVERIFIED item 1). Covers every currency in
# engine/rates/conventions.py::CCY_RFR (== rates_marketdata.OIS_INDEX's key set).
CCY_VOL_PREFIX: Dict[str, str] = {
    "USD": "US",
    "EUR": "EU",
    "GBP": "BP",
    "JPY": "JY",
    "CHF": "SF",
    "CAD": "CD",
    "AUD": "AD",
}

# Sub-year expiry -> single-letter code (UNVERIFIED item 5), borrowed from
# rates_marketdata.py's OIS depo-tenor lettering (A=1M, C=3M, F=6M).
_EXPIRY_LETTER = {"1M": "A", "3M": "C", "6M": "F"}


class MarketDataError(Exception):
    """Base error for this market data layer (own class, not shared with the other two
    market-data modules in this directory -- see module docstring)."""


class MarketDataUnavailable(MarketDataError):
    """Requested rate-vol data cannot be produced (wrong as_of, unknown ccy in a file
    snapshot)."""


class TickerMapError(Exception):
    pass


# --------------------------------------------------------------------------- ticker map

def normalize_ccy(ccy: str) -> str:
    return ccy.strip().upper()


def normalize_tenor(tenor: str) -> str:
    return tenor.strip().upper()


def normalize_instrument(instrument: str) -> str:
    return instrument.strip().upper()


def normalize_vol_type(vol_type: str) -> str:
    return vol_type.strip().upper()


def _expiry_code(expiry_tenor: str) -> str:
    """UNVERIFIED item 5. Letter for 1M/3M/6M, else zero-padded two-digit year number."""
    t = normalize_tenor(expiry_tenor)
    if t in _EXPIRY_LETTER:
        return _EXPIRY_LETTER[t]
    if t.endswith("Y"):
        try:
            n = int(t[:-1])
        except ValueError:
            raise TickerMapError(f"Cannot encode expiry tenor: {expiry_tenor!r}")
        return f"{n:02d}"
    raise TickerMapError(
        f"Cannot encode expiry tenor: {expiry_tenor!r}; expected one of "
        f"{sorted(_EXPIRY_LETTER)} or an '<N>Y' tenor"
    )


def _tenor_code(underlying_tenor: str) -> str:
    """UNVERIFIED item 5. Zero-padded two-digit year number (underlying tenors in this
    module's scope are always >= 1Y, so no letter case is needed here)."""
    t = normalize_tenor(underlying_tenor)
    if not t.endswith("Y"):
        raise TickerMapError(f"Cannot encode underlying tenor: {underlying_tenor!r}; expected '<N>Y'")
    try:
        n = int(t[:-1])
    except ValueError:
        raise TickerMapError(f"Cannot encode underlying tenor: {underlying_tenor!r}")
    return f"{n:02d}"


def rate_vol_ticker(
    ccy: str, instrument: str, expiry_tenor: str, underlying_tenor: str = "",
    vol_type: str = "LOGNORMAL",
) -> str:
    """Build the (UNVERIFIED) Bloomberg ticker for one (ccy, instrument, expiry,
    underlying tenor, vol_type) -- see module docstring items 1-5. `underlying_tenor`
    is ignored for instrument='CAP' (caps have no underlying-tenor axis)."""
    ccy_u = normalize_ccy(ccy)
    prefix = CCY_VOL_PREFIX.get(ccy_u)
    if prefix is None:
        raise TickerMapError(f"No vol ticker prefix for {ccy_u!r}; expected one of {sorted(CCY_VOL_PREFIX)}")

    vol_type_u = normalize_vol_type(vol_type)
    instrument_u = normalize_instrument(instrument)

    if instrument_u == "SWAPTION":
        infix = {"LOGNORMAL": "SV", "NORMAL": "SN"}.get(vol_type_u)
        if infix is None:
            raise TickerMapError(f"Unknown vol_type {vol_type!r}; expected LOGNORMAL or NORMAL")
        if not underlying_tenor:
            raise TickerMapError("underlying_tenor is required for a SWAPTION ticker")
        return f"{prefix}{infix}{_expiry_code(expiry_tenor)}{_tenor_code(underlying_tenor)} Curncy"

    if instrument_u == "CAP":
        infix = {"LOGNORMAL": "CV", "NORMAL": "CN"}.get(vol_type_u)
        if infix is None:
            raise TickerMapError(f"Unknown vol_type {vol_type!r}; expected LOGNORMAL or NORMAL")
        return f"{prefix}{infix}{_expiry_code(expiry_tenor)} Curncy"

    raise TickerMapError(f"Unknown instrument {instrument!r}; expected SWAPTION or CAP")


def tenor_to_years(tenor: str) -> float:
    """Approximate a tenor string (expiry or underlying) to a year fraction, for
    sorting/interpolation only (not accrual) -- 'NM' -> N/12, 'NY' -> N."""
    t = normalize_tenor(tenor)
    if not t:
        raise ValueError(f"Cannot parse tenor: {tenor!r}")
    unit = t[-1]
    try:
        number = int(t[:-1])
    except ValueError:
        raise ValueError(f"Cannot parse tenor: {tenor!r}")
    if unit == "M":
        return number / 12.0
    if unit == "Y":
        return float(number)
    raise ValueError(f"Cannot parse tenor: {tenor!r}")


def rate_vol_grid_needed(
    ccys: Sequence[str],
    instruments: Sequence[str] = INSTRUMENTS,
    vol_types: Sequence[str] = VOL_TYPES,
    expiries: Sequence[str] = EXPIRY_TENORS,
    underlying_tenors: Sequence[str] = UNDERLYING_TENORS,
) -> List[Tuple[str, str, str, str, str, str]]:
    """(ccy, instrument, expiry_tenor, underlying_tenor, vol_type, ticker) for every
    combination in scope. `underlying_tenor` is '' for instrument='CAP' (see
    rate_vol_ticker)."""
    needed: List[Tuple[str, str, str, str, str, str]] = []
    for ccy in ccys:
        ccy_u = normalize_ccy(ccy)
        for instrument in instruments:
            instrument_u = normalize_instrument(instrument)
            for vol_type in vol_types:
                vol_type_u = normalize_vol_type(vol_type)
                if instrument_u == "CAP":
                    for expiry in expiries:
                        ticker = rate_vol_ticker(ccy_u, instrument_u, expiry, "", vol_type_u)
                        needed.append((ccy_u, instrument_u, expiry, "", vol_type_u, ticker))
                else:
                    for expiry in expiries:
                        for tenor in underlying_tenors:
                            ticker = rate_vol_ticker(ccy_u, instrument_u, expiry, tenor, vol_type_u)
                            needed.append((ccy_u, instrument_u, expiry, tenor, vol_type_u, ticker))
    return needed


# --------------------------------------------------------------------------- dataclasses

def _date_to_str(value: Optional[date]) -> Optional[str]:
    return None if value is None else value.isoformat()


def _str_to_date(value: Optional[str]) -> Optional[date]:
    return None if value is None else date.fromisoformat(value)


@dataclass
class RateVolQuote:
    instrument: str            # SWAPTION | CAP
    expiry_tenor: str
    underlying_tenor: str      # '' for CAP
    vol_type: str
    ticker: str
    value: float
    quote_type: str = "ATM"
    strike_offset_bp: float = 0.0
    field: str = FIELD
    source: str = "BBG"

    def __post_init__(self) -> None:
        self.instrument = normalize_instrument(self.instrument)
        self.expiry_tenor = normalize_tenor(self.expiry_tenor)
        self.underlying_tenor = normalize_tenor(self.underlying_tenor) if self.underlying_tenor else ""
        self.vol_type = normalize_vol_type(self.vol_type)

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "expiry_tenor": self.expiry_tenor,
            "underlying_tenor": self.underlying_tenor, "quote_type": self.quote_type,
            "strike_offset_bp": self.strike_offset_bp, "vol_type": self.vol_type,
            "ticker": self.ticker, "value": self.value, "field": self.field, "source": self.source,
        }

    @staticmethod
    def from_dict(data: dict) -> "RateVolQuote":
        return RateVolQuote(
            instrument=data["instrument"], expiry_tenor=data["expiry_tenor"],
            underlying_tenor=data.get("underlying_tenor", ""), vol_type=data["vol_type"],
            ticker=data["ticker"], value=float(data["value"]),
            quote_type=data.get("quote_type", "ATM"),
            strike_offset_bp=float(data.get("strike_offset_bp", 0.0)),
            field=data.get("field", FIELD), source=data.get("source", "BBG"),
        )


@dataclass
class CcyRateVolSnapshot:
    ccy: str
    index: str
    as_of: date
    quotes: List[RateVolQuote] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ccy": self.ccy, "index": self.index, "as_of": _date_to_str(self.as_of),
            "quotes": [q.to_dict() for q in self.quotes],
        }

    @staticmethod
    def from_dict(data: dict) -> "CcyRateVolSnapshot":
        return CcyRateVolSnapshot(
            ccy=data["ccy"], index=data["index"], as_of=_str_to_date(data["as_of"]),
            quotes=[RateVolQuote.from_dict(q) for q in data.get("quotes", [])],
        )


@dataclass
class RateVolSnapshot:
    """On-disk shape for RateVolFileSource -- one as_of, many currencies. Mirrors
    vol_marketdata.VolSnapshot (pair -> currency)."""
    as_of: date
    source: str
    currencies: Dict[str, CcyRateVolSnapshot] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "as_of": _date_to_str(self.as_of), "source": self.source,
            "currencies": {k: v.to_dict() for k, v in self.currencies.items()},
        }

    @staticmethod
    def from_dict(data: dict) -> "RateVolSnapshot":
        return RateVolSnapshot(
            as_of=_str_to_date(data["as_of"]), source=data["source"],
            currencies={k: CcyRateVolSnapshot.from_dict(v) for k, v in data.get("currencies", {}).items()},
        )


@dataclass
class RateVolFetchResult:
    """Returned by both sources' get_rate_vol_quotes. `diagnostics` carries one entry
    per ticker that could not be resolved -- callers inspect this instead of an
    exception; a missing ticker never raises (see module docstring)."""
    as_of: date
    source: str
    currencies: Dict[str, CcyRateVolSnapshot] = field(default_factory=dict)
    diagnostics: List[dict] = field(default_factory=list)


def load_rate_vol_snapshot(path: Union[str, Path]) -> RateVolSnapshot:
    with open(path, "r", encoding="utf-8") as f:
        return RateVolSnapshot.from_dict(json.load(f))


def save_rate_vol_snapshot(snap: RateVolSnapshot, path: Union[str, Path]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------- RateVolFileSource

class RateVolFileSource:
    """FileSource-equivalent for rate-vol quotes: never touches blpapi, reads a saved
    RateVolSnapshot (JSON path or already-loaded). Mirrors VolFileSource / RatesFileSource."""

    def __init__(self, snapshot_or_path: Union[RateVolSnapshot, str, Path]) -> None:
        if isinstance(snapshot_or_path, RateVolSnapshot):
            self._snapshot = snapshot_or_path
        else:
            self._snapshot = load_rate_vol_snapshot(snapshot_or_path)

    def name(self) -> str:
        return f"RateVolFileSource({self._snapshot.source})"

    def get_rate_vol_quotes(
        self, ccys: Sequence[str], as_of: Optional[date] = None,
        instruments: Optional[Sequence[str]] = None,
        vol_types: Optional[Sequence[str]] = None,
        expiries: Optional[Sequence[str]] = None,
        underlying_tenors: Optional[Sequence[str]] = None,
    ) -> RateVolFetchResult:
        """`instruments`/`vol_types`/`expiries`/`underlying_tenors` are accepted for
        interface parity with RateVolBloombergSource (used by --probe to narrow to one
        grid point) but have no effect here: a file snapshot already contains whichever
        grid it contains -- same "tenors is accepted but a no-op" choice as
        VolFileSource.get_vol_quotes."""
        if as_of is not None and as_of != self._snapshot.as_of:
            raise MarketDataUnavailable(
                f"Snapshot is as of {self._snapshot.as_of}, requested {as_of}"
            )
        diagnostics: List[dict] = []
        currencies_out: Dict[str, CcyRateVolSnapshot] = {}
        for ccy in ccys:
            key = normalize_ccy(ccy)
            snap = self._snapshot.currencies.get(key)
            if snap is None:
                diagnostics.append({
                    "ccy": key, "status": "MISSING",
                    "detail": f"no snapshot for ccy; available: {sorted(self._snapshot.currencies)}",
                })
                continue
            currencies_out[key] = snap
        return RateVolFetchResult(
            as_of=self._snapshot.as_of, source=self._snapshot.source,
            currencies=currencies_out, diagnostics=diagnostics,
        )


# --------------------------------------------------------------------------- RateVolBloombergSource

_MAX_CONSECUTIVE_TIMEOUTS = 3


class RateVolBloombergSource:
    """Live blpapi wrapper for rate-vol quotes. Opens its OWN blpapi.Session -- see
    module docstring. Every ticker/field name used here is UNVERIFIED (see module
    docstring numbered list); a per-ticker failure is recorded in the returned
    RateVolFetchResult's diagnostics, never raised -- only session/service-open
    failures raise MarketDataError."""

    def __init__(self, host: str = "localhost", port: int = 8194, timeout_ms: int = 30000) -> None:
        try:
            import blpapi  # noqa: F401
        except ImportError as exc:
            raise MarketDataError(
                "blpapi not installed. Install the Bloomberg Python API on the terminal "
                "machine (see WAPI <GO>), or use RateVolFileSource with a saved snapshot."
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
        return f"RateVolBloombergSource({self.host}:{self.port})"

    def close(self) -> None:
        try:
            self._session.stop()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "RateVolBloombergSource":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- internal: request/response, named-element access (see vol_marketdata.py /
    # rates_marketdata.py for the pattern this matches) -----------------------------------

    def _fetch(self, tickers: Sequence[str], as_of: Optional[date]):
        """Returns (values: {ticker: raw value or None}, detail: Optional[str]). Never
        raises -- see class docstring."""
        blpapi = self._blpapi
        try:
            live = as_of is None
            request_type = "ReferenceDataRequest" if live else "HistoricalDataRequest"
            request = self._service.createRequest(request_type)
            for t in tickers:
                request.getElement("securities").appendValue(t)
            request.getElement("fields").appendValue(FIELD)
            if not live:
                d = as_of.strftime("%Y%m%d")
                request.set("startDate", d)
                request.set("endDate", d)

            self._session.sendRequest(request)

            out: Dict[str, object] = {}
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
                            value = None
                            if sd.hasElement("fieldData"):
                                fd = sd.getElement("fieldData")
                                if fd.hasElement(FIELD):
                                    value = fd.getElement(FIELD).getValue()
                            out[ticker] = value
                    else:
                        ticker = sec_data.getElementAsString("security")
                        value = None
                        if sec_data.hasElement("fieldData"):
                            fd = sec_data.getElement("fieldData")
                            if fd.numValues() > 0:
                                point = fd.getValueAsElement(0)
                                if point.hasElement(FIELD):
                                    value = point.getElement(FIELD).getValue()
                        out[ticker] = value
                if event_type == blpapi.Event.RESPONSE and event_is_ours:
                    break
            return out, None
        except Exception as exc:  # noqa: BLE001 -- never let a single batch crash the pull
            return {}, f"{type(exc).__name__}: {exc}"

    def get_rate_vol_quotes(
        self, ccys: Sequence[str], as_of: Optional[date] = None,
        instruments: Optional[Sequence[str]] = None,
        vol_types: Optional[Sequence[str]] = None,
        expiries: Optional[Sequence[str]] = None,
        underlying_tenors: Optional[Sequence[str]] = None,
    ) -> RateVolFetchResult:
        """Live ReferenceDataRequest (as_of=None) or HistoricalDataRequest (as_of given)
        for every (ccy, instrument, expiry, underlying_tenor, vol_type) ticker, batched
        into one request. Per-ticker failures are recorded in the result's
        `diagnostics`, never raised -- see class docstring."""
        effective_as_of = as_of if as_of is not None else date.today()
        instruments = list(instruments) if instruments is not None else list(INSTRUMENTS)
        vol_types = list(vol_types) if vol_types is not None else list(VOL_TYPES)
        expiries = list(expiries) if expiries is not None else list(EXPIRY_TENORS)
        underlying_tenors = list(underlying_tenors) if underlying_tenors is not None else list(UNDERLYING_TENORS)

        needed = rate_vol_grid_needed(ccys, instruments, vol_types, expiries, underlying_tenors)
        all_tickers = sorted({n[5] for n in needed})
        values, batch_detail = self._fetch(all_tickers, as_of) if all_tickers else ({}, None)

        diagnostics: List[dict] = []
        currencies_out: Dict[str, CcyRateVolSnapshot] = {}
        for ccy, instrument, expiry, tenor, vol_type, ticker in needed:
            val = values.get(ticker)
            if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
                diagnostics.append({
                    "ccy": ccy, "instrument": instrument, "expiry_tenor": expiry,
                    "underlying_tenor": tenor, "vol_type": vol_type, "ticker": ticker,
                    "status": "MISSING",
                    "detail": batch_detail or "no value returned for this ticker",
                })
                continue
            index = OIS_INDEX.get(ccy, "")
            currencies_out.setdefault(ccy, CcyRateVolSnapshot(ccy=ccy, index=index, as_of=effective_as_of, quotes=[]))
            currencies_out[ccy].quotes.append(RateVolQuote(
                instrument=instrument, expiry_tenor=expiry, underlying_tenor=tenor,
                vol_type=vol_type, ticker=ticker, value=float(val), field=FIELD, source="BBG",
            ))

        return RateVolFetchResult(
            as_of=effective_as_of, source="BBG_BDP" if as_of is None else "BBG_BDH",
            currencies=currencies_out, diagnostics=diagnostics,
        )


# --------------------------------------------------------------------------- rate_vol_quotes staging table

RATE_VOL_QUOTES_COLUMNS = [
    "as_of_date", "ccy", "index", "instrument", "expiry_tenor", "underlying_tenor",
    "quote_type", "strike_offset_bp", "value", "vol_type", "ticker", "field", "source", "snapped_at",
]

_RATE_VOL_QUOTES_DDL = """
CREATE TABLE IF NOT EXISTS rate_vol_quotes (
  as_of_date        TEXT NOT NULL,
  ccy               TEXT NOT NULL,
  "index"           TEXT NOT NULL,
  instrument        TEXT NOT NULL,
  expiry_tenor      TEXT NOT NULL,
  underlying_tenor  TEXT NOT NULL,
  quote_type        TEXT NOT NULL,
  strike_offset_bp  REAL NOT NULL,
  value             REAL NOT NULL,
  vol_type          TEXT NOT NULL,
  ticker            TEXT NOT NULL,
  field             TEXT NOT NULL,
  source            TEXT NOT NULL,
  snapped_at        TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, "index", instrument, expiry_tenor, underlying_tenor, strike_offset_bp, source)
);
"""


def ensure_rate_vol_quotes_table(conn: sqlite3.Connection) -> None:
    conn.execute(_RATE_VOL_QUOTES_DDL)


def write_rate_vol_quotes(
    conn: sqlite3.Connection,
    snapshot: Union[RateVolFetchResult, CcyRateVolSnapshot, Dict[str, CcyRateVolSnapshot]],
    as_of_date: str,
    source: str = "BBG_BDP",
) -> int:
    """Insert one row per quote into rate_vol_quotes (creating the table defensively if
    it doesn't exist -- see module docstring). Accepts a RateVolFetchResult (the normal
    case), a single CcyRateVolSnapshot, or a plain {ccy: CcyRateVolSnapshot} dict.

    Uses INSERT OR REPLACE keyed on
    (as_of_date, ccy, index, instrument, expiry_tenor, underlying_tenor,
    strike_offset_bp, source) -- NOTE this key does not include vol_type (see module
    docstring "Known schema quirk"): writing both a LOGNORMAL and a NORMAL quote for the
    same node/source overwrites, last-in-the-iteration-order wins. Returns the number of
    rows attempted (== rows in `snapshot`, not necessarily the number of distinct rows
    left in the table afterwards, same "len(rows) processed" convention as
    vol_marketdata.write_vol_quotes).
    """
    ensure_rate_vol_quotes_table(conn)
    if isinstance(snapshot, RateVolFetchResult):
        currencies = snapshot.currencies
    elif isinstance(snapshot, CcyRateVolSnapshot):
        currencies = {snapshot.ccy: snapshot}
    elif isinstance(snapshot, dict):
        currencies = snapshot
    else:
        raise TypeError(f"Unsupported snapshot type for write_rate_vol_quotes: {type(snapshot)!r}")

    stamp = snapped_at(date.fromisoformat(as_of_date))
    rows = [
        (
            as_of_date, ccy, snap.index, q.instrument, q.expiry_tenor, q.underlying_tenor,
            q.quote_type, float(q.strike_offset_bp), float(q.value), q.vol_type, q.ticker,
            q.field, source, stamp,
        )
        for ccy, snap in currencies.items() for q in snap.quotes
    ]
    with conn:
        conn.executemany(
            'INSERT OR REPLACE INTO rate_vol_quotes '
            '(as_of_date, ccy, "index", instrument, expiry_tenor, underlying_tenor, quote_type, '
            'strike_offset_bp, value, vol_type, ticker, field, source, snapped_at) '
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


# --------------------------------------------------------------------------- read helpers (for rates_vol / rates-exotics)

def _pick_source(sources: set) -> str:
    """Never blend sources for one grid -- same rule as vol_marketdata._pick_source /
    engine/rates/store.py's _read_curve_quotes: prefer BBG_BDP, else BBG_BDH, else
    whichever source sorts first alphabetically, for determinism."""
    if "BBG_BDP" in sources:
        return "BBG_BDP"
    if "BBG_BDH" in sources:
        return "BBG_BDH"
    return sorted(sources)[0]


def rate_vol_grid(
    conn: sqlite3.Connection, as_of: str, ccy: str, instrument: str, vol_type: str,
) -> Dict[Tuple[str, str], float]:
    """{(expiry_tenor, underlying_tenor): value} for ATM quotes (as_of, ccy, instrument,
    vol_type) from rate_vol_quotes, using a single preferred source (see _pick_source)
    -- never blends sources. `underlying_tenor` is '' for instrument='CAP'. Empty dict
    if nothing is staged."""
    ensure_rate_vol_quotes_table(conn)
    rows = conn.execute(
        'SELECT expiry_tenor, underlying_tenor, value, source FROM rate_vol_quotes '
        'WHERE as_of_date = ? AND ccy = ? AND instrument = ? AND vol_type = ? AND quote_type = ?',
        (as_of, normalize_ccy(ccy), normalize_instrument(instrument), normalize_vol_type(vol_type), "ATM"),
    ).fetchall()
    if not rows:
        return {}
    source = _pick_source({r[3] for r in rows})
    grid: Dict[Tuple[str, str], float] = {}
    for expiry_tenor, underlying_tenor, value, src in rows:
        if src != source:
            continue
        grid[(expiry_tenor, underlying_tenor)] = value
    return grid


def _bracket(sorted_pairs: List[Tuple[str, float]], target: float) -> Optional[Tuple[Tuple[str, float], Tuple[str, float]]]:
    """First/last pair from `sorted_pairs` (sorted ascending by the numeric 2nd element)
    such that pairs[i][1] <= target <= pairs[i+1][1]. None if `target` is outside the
    range spanned by `sorted_pairs` -- callers never extrapolate."""
    if target < sorted_pairs[0][1] or target > sorted_pairs[-1][1]:
        return None
    for lo, hi in zip(sorted_pairs, sorted_pairs[1:]):
        if lo[1] <= target <= hi[1]:
            return lo, hi
    return sorted_pairs[-2], sorted_pairs[-1]  # pragma: no cover -- unreachable given the bounds check above


def atm_swaption_vol(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    expiry_date: Union[str, date],
    underlying_tenor_years: float,
    vol_type: str,
    as_of_date_for_tenor_arith: Union[str, date],
) -> Optional[float]:
    """Bilinear interpolation of the ATM swaption vol grid staged for (as_of, ccy,
    vol_type): variance-time interpolation across the expiry axis (same convention as
    vol_marketdata.atm_vol_for_expiry), plain linear interpolation across the
    underlying-tenor axis, in that order (interpolate tenor first at each of the two
    bracketing expiry nodes, then interpolate expiry between those two tenor-interpolated
    values).

    `as_of_date_for_tenor_arith` is kept separate from `as_of` (the rate_vol_quotes
    lookup key) so that a grid staged under one as_of can still be interpreted against a
    different pricing/reference date for the calendar-day arithmetic that turns each
    expiry_tenor node ('1M', '2Y', ...) into a day offset comparable to `expiry_date` --
    mirrors the as_of / evaluationDate split documented in
    engine/rates_vol/inputs.py's module docstring "Numerical quirk" section. Ordinarily
    the two dates are the same.

    Returns None (never extrapolates, never interpolates across a hole in a sparse
    grid) if: fewer than 2 distinct expiry nodes or fewer than 2 distinct underlying-
    tenor nodes are staged; `expiry_date` falls outside the staged expiry range;
    `underlying_tenor_years` falls outside the staged tenor range; or any of the 4
    grid corners needed for the bracket is missing (a sparse/ragged grid).

    Note: because tenor is interpolated first (in plain vol terms) and expiry second
    (in variance-time), a result that needs interpolation on BOTH axes is not
    guaranteed to fall strictly within the min/max of the 4 raw corner values -- the
    two variance-time-interpolated inputs (vol_e0, vol_e1) are themselves already
    tenor-interpolated, not raw corners, so the usual "variance-time interpolation
    stays between its two inputs" guarantee applies to those two inputs, not to the
    original 4 corners. When only one axis needs interpolation (the other lands
    exactly on a staged node) the result IS bounded by the two relevant nodes.
    """
    grid = rate_vol_grid(conn, as_of, ccy, "SWAPTION", vol_type)
    if not grid:
        return None

    expiry_nodes = sorted({e for e, _ in grid})
    tenor_nodes = sorted({t for _, t in grid}, key=tenor_to_years)
    if len(expiry_nodes) < 2 or len(tenor_nodes) < 2:
        return None

    ref_date = date.fromisoformat(as_of_date_for_tenor_arith) if isinstance(as_of_date_for_tenor_arith, str) else as_of_date_for_tenor_arith
    target_date = date.fromisoformat(expiry_date) if isinstance(expiry_date, str) else expiry_date
    target_days = (target_date - ref_date).days

    expiry_days_sorted = sorted(
        ((e, round(tenor_to_years(e) * 365)) for e in expiry_nodes), key=lambda p: p[1]
    )
    e_bracket = _bracket(expiry_days_sorted, target_days)
    if e_bracket is None:
        return None
    (e0, d0), (e1, d1) = e_bracket

    tenor_years_sorted = sorted(((t, tenor_to_years(t)) for t in tenor_nodes), key=lambda p: p[1])
    t_bracket = _bracket(tenor_years_sorted, underlying_tenor_years)
    if t_bracket is None:
        return None
    (t0, y0), (t1, y1) = t_bracket

    v00, v01 = grid.get((e0, t0)), grid.get((e0, t1))
    v10, v11 = grid.get((e1, t0)), grid.get((e1, t1))
    if None in (v00, v01, v10, v11):
        return None

    frac_t = 0.0 if y1 == y0 else (underlying_tenor_years - y0) / (y1 - y0)
    vol_e0 = v00 + frac_t * (v01 - v00)
    vol_e1 = v10 + frac_t * (v11 - v10)

    if d1 == d0 or target_days == 0:
        return vol_e0
    var0 = (vol_e0 ** 2) * d0
    var1 = (vol_e1 ** 2) * d1
    frac_e = (target_days - d0) / (d1 - d0)
    var_t = var0 + frac_e * (var1 - var0)
    return (var_t / target_days) ** 0.5


def to_rate_vols_rows(
    conn: sqlite3.Connection, as_of: str, ccy: str, instrument: str, vol_type: str,
    source: Optional[str] = None,
) -> List[dict]:
    """Read the single-source ATM grid for (as_of, ccy, instrument, vol_type) from
    rate_vol_quotes and return rows already shaped for
    engine.rates_vol.inputs.set_manual_rate_vol / its `rate_vols` table -- dicts with
    keys as_of_date, ccy, index, expiry_tenor_or_date, underlying_tenor, strike_or_ATM,
    vol, vol_type, source. `expiry_tenor_or_date` is this module's tenor string (e.g.
    '1Y'), not an exact option expiry date -- these are generic ATM fallback nodes (this
    module has no notion of one option's own expiry), so a caller wiring an exact-strike
    or exact-expiry vol still resolves that through `atm_swaption_vol` / `rate_vol_grid`
    directly. `underlying_tenor` is '' for CAP rows; `strike_or_ATM` is always the
    literal 'ATM' (SKEW rows are never produced -- see module docstring item 9). This
    function only reads and reshapes; it never writes to `rate_vols` itself (that table
    is owned by engine/rates_vol, not this module) -- the caller passes the returned
    rows to `set_manual_rate_vol` (or another writer) itself, "one call" referring to
    calling this function once per (ccy, instrument, vol_type) to get every grid node.
    """
    ensure_rate_vol_quotes_table(conn)
    ccy_u, instrument_u, vol_type_u = normalize_ccy(ccy), normalize_instrument(instrument), normalize_vol_type(vol_type)
    rows = conn.execute(
        'SELECT expiry_tenor, underlying_tenor, value, source FROM rate_vol_quotes '
        'WHERE as_of_date = ? AND ccy = ? AND instrument = ? AND vol_type = ? AND quote_type = ?',
        (as_of, ccy_u, instrument_u, vol_type_u, "ATM"),
    ).fetchall()
    if not rows:
        return []
    picked_source = source if source is not None else _pick_source({r[3] for r in rows})
    index = OIS_INDEX.get(ccy_u, "")
    out: List[dict] = []
    for expiry_tenor, underlying_tenor, value, src in rows:
        if src != picked_source:
            continue
        out.append({
            "as_of_date": as_of, "ccy": ccy_u, "index": index,
            "expiry_tenor_or_date": expiry_tenor, "underlying_tenor": underlying_tenor,
            "strike_or_ATM": "ATM", "vol": value, "vol_type": vol_type_u, "source": picked_source,
        })
    return out


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
    parser.add_argument("--file", help="path to a saved rate-vol snapshot JSON (RateVolFileSource, offline mode); omit for the live RateVolBloombergSource")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--probe", action="store_true",
                         help="probe mode: request one swaption ATM lognormal ticker, one swaption ATM "
                              "normal ticker and one cap ATM lognormal ticker for USD 1Yx10Y and print what "
                              "came back (or the failure) -- run this on the Bloomberg terminal to check the "
                              "ticker/field guesses in the module docstring. Writes <db>.diag.json; no "
                              "rate_vol_quotes rows written.")
    return parser


def _open_source(args) -> Union[RateVolBloombergSource, RateVolFileSource]:
    if args.file:
        return RateVolFileSource(args.file)
    return RateVolBloombergSource(args.host, args.port)


_PROBE_CCY = "USD"
_PROBE_EXPIRY = "1Y"
_PROBE_TENOR = "10Y"


def _run_probe(args, diag: dict) -> int:
    diag["probe"] = {"ccy": _PROBE_CCY, "expiry_tenor": _PROBE_EXPIRY, "underlying_tenor": _PROBE_TENOR}
    as_of_date = date.fromisoformat(args.as_of) if args.as_of else None

    try:
        source = _open_source(args)
    except MarketDataError as e:
        diag["summary"] = {"outcome": "FAILED", "exit_code": 4, "error": str(e)}
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    try:
        result = source.get_rate_vol_quotes(
            [_PROBE_CCY], as_of=as_of_date, instruments=["SWAPTION", "CAP"],
            vol_types=["LOGNORMAL", "NORMAL"], expiries=[_PROBE_EXPIRY], underlying_tenors=[_PROBE_TENOR],
        )
    finally:
        if hasattr(source, "close"):
            source.close()

    snap = result.currencies.get(_PROBE_CCY)
    quotes = snap.quotes if snap else []

    probe_specs = [
        ("SWAPTION", "LOGNORMAL", _PROBE_TENOR),
        ("SWAPTION", "NORMAL", _PROBE_TENOR),
        ("CAP", "LOGNORMAL", ""),
    ]
    steps = []
    for instrument, vol_type, tenor in probe_specs:
        ticker = rate_vol_ticker(_PROBE_CCY, instrument, _PROBE_EXPIRY, tenor, vol_type)
        match = next(
            (q for q in quotes if q.instrument == instrument and q.vol_type == vol_type and q.underlying_tenor == tenor),
            None,
        )
        value = match.value if match else None
        outcome = "OK" if value is not None else "MISSING"
        step_diag = [
            d for d in result.diagnostics
            if d.get("instrument") == instrument and d.get("vol_type") == vol_type and d.get("underlying_tenor") == tenor
        ]
        steps.append({
            "instrument": instrument, "vol_type": vol_type, "ticker": ticker,
            "outcome": outcome, "value": value, "diagnostics": step_diag,
        })
        print(f"PROBE {instrument:>8} {vol_type:>9} {ticker}: {outcome} value={value}", file=sys.stderr)

    failures = [s for s in steps if s["outcome"] != "OK"]
    diag["probe"]["steps"] = steps
    diag["summary"] = {
        "outcome": "OK" if not failures else "PROBE_COMPLETE_WITH_FAILURES",
        "exit_code": 0, "failures": len(failures),
    }
    return 0


def _requested_count(ccys: Sequence[str]) -> int:
    total = 0
    for instrument in INSTRUMENTS:
        if instrument == "CAP":
            total += len(ccys) * len(VOL_TYPES) * len(EXPIRY_TENORS)
        else:
            total += len(ccys) * len(VOL_TYPES) * len(EXPIRY_TENORS) * len(UNDERLYING_TENORS)
    return total


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
        ccys = sorted(CCY_VOL_PREFIX)
        diag["ccys"] = ccys

        try:
            source = _open_source(args)
        except MarketDataError as e:
            diag["summary"] = {"outcome": "FAILED", "exit_code": 4, "error": str(e)}
            print(f"ERROR: {e}", file=sys.stderr)
            return 4

        try:
            result = source.get_rate_vol_quotes(ccys, as_of=as_of_date)
        finally:
            if hasattr(source, "close"):
                source.close()

        diag["fetch_diagnostics"] = result.diagnostics
        write_source = result.source
        n = write_rate_vol_quotes(conn, result, as_of_date=args.as_of, source=write_source)
        conn.commit()

        requested = _requested_count(ccys)
        written = sum(len(snap.quotes) for snap in result.currencies.values())
        partial = written < requested
        diag["summary"] = {
            "outcome": "OK" if not partial else "PARTIAL",
            "exit_code": 0 if not partial else 5,
            "requested": requested, "written": written, "rows_written_to_db": n,
            "source": write_source,
        }
        if partial:
            print(f"ERROR: only {written}/{requested} requested rate-vol quotes obtained; "
                  f"{n} row(s) written to {args.db} -- see fetch_diagnostics in the diag JSON", file=sys.stderr)
        print(f"wrote {n} rate_vol_quotes row(s) to {args.db} ({written}/{requested} requested quotes obtained)",
              file=sys.stderr)
        return 0 if not partial else 5
    finally:
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: ``py -m data.bloomberg.rates_vol_marketdata``.

    Exit codes (mirrors vol_marketdata.py's / pull_marks.py's table):
      0  OK -- pull: every requested (ccy, instrument, expiry, underlying_tenor,
         vol_type) quote was written; probe: the source opened and every step ran
         (individual step outcomes are exploratory, not pass/fail for the probe run
         itself -- see summary.failures).
      2  bad --as-of (missing, for a normal pull, or not a valid ISO date).
      3  unhandled exception.
      4  source open failure (blpapi not installed, session/service open failed, or
         RateVolFileSource given a bad path).
      5  partial pull -- one or more requested keys were not written (see
         fetch_diagnostics in the diag JSON for why, per ticker).

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
