"""Bloomberg OIS-curve market-data layer, ported from the reference "Rates Swap
Calculator" project (swapcalc/marketdata/{base,bloomberg,tickers,filesource}.py).

Scope for this phase: single-currency OIS curves only -- USD SOFR, EUR ESTR,
GBP SONIA, JPY TONA, CHF SARON, CAD CORRA, AUD AONIA. Term-rate, basis and
cross-currency curves from the reference project are deliberately NOT ported
(out of scope; see the task that created this module).

Two source implementations, both satisfying the same informal interface
(get_curve_quotes / get_fixings / get_bbg_curve):
  - RatesBloombergSource: live blpapi wrapper. Requires the `blpapi` package
    (raises MarketDataError with an install hint if it is missing) and a
    running Bloomberg Terminal / B-PIPE.
  - RatesFileSource: reads a saved JSON snapshot (see data/bloomberg/fixtures/
    ois_snapshot_v1.json for the shape); used by tests and for offline work.
    Mirrors the reference project's FileSource pattern.

NOTE on sessions: RatesBloombergSource opens its OWN blpapi.Session, separate
from pull_marks.py's session. This is deliberate for this task -- merging the
two into one shared session is flagged as a follow-up and is tracked by the
housekeeper in docs/open-questions.md, not addressed here.

get_bbg_curve is for reconciliation only, mirroring how CLAUDE.md treats
BBG_BDH as reconciliation-only for PAR_RATE/PV_USD/DV01_USD (and BNP_BVAL for
FX): it returns Bloomberg's own built curve (YCSW.... Index, CURVE_TENOR_RATES)
to check our own bootstrapped curve against, never to build our curve from
directly.

Writes go to the `curve_quotes` staging table (owned by data/ingest/schema.py,
being added there in a parallel task). This module does not assume that table
already exists: write_curve_quotes() creates it defensively with
CREATE TABLE IF NOT EXISTS using the exact shape described in the task, so it
works whether or not data-ingest's migration has landed yet, and is a no-op
once it has (assuming the same column set).
"""
from __future__ import annotations

import datetime
import decimal
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

_MIN_QUOTES = 4
_MAX_CONSECUTIVE_TIMEOUTS = 3

# CCY -> OIS index name in scope for this phase (CLAUDE.md-adjacent: this module
# owns its own mapping, curve_quotes.index mirrors this value).
OIS_INDEX = {
    "USD": "SOFR",
    "EUR": "ESTR",
    "GBP": "SONIA",
    "JPY": "TONA",
    "CHF": "SARON",
    "CAD": "CORRA",
    "AUD": "AONIA",
}


# --------------------------------------------------------------------------- dataclasses
# Ported from swapcalc/marketdata/base.py, OIS-only (no QuoteType.DEPO/FRA/SWAP/FUT/BASIS
# variants -- term-rate, basis and XCCY curves are out of scope).

def _decimal_to_str(value: Optional[decimal.Decimal]) -> Optional[str]:
    return None if value is None else str(value)


def _str_to_decimal(value: Optional[str]) -> Optional[decimal.Decimal]:
    return None if value is None else decimal.Decimal(value)


def _date_to_str(value: Optional[datetime.date]) -> Optional[str]:
    return None if value is None else value.isoformat()


def _str_to_date(value: Optional[str]) -> Optional[datetime.date]:
    return None if value is None else datetime.date.fromisoformat(value)


def normalize_tenor(tenor: str) -> str:
    """Normalise a tenor string to uppercase (e.g. '1w' -> '1W')."""
    return tenor.strip().upper()


def tenor_to_days(tenor: str) -> int:
    """Approximate a tenor string to a day count, for sorting only (not accrual).

    Units: D (days), W or Z (weeks -- 'Z' is the Bloomberg USOSFR-style weekly
    ticker convention, e.g. USOSFR1Z = 1 week), M (30 days), Y (365 days).
    """
    t = normalize_tenor(tenor)
    if not t:
        raise ValueError(f"Cannot parse tenor for sorting: {tenor!r}")
    unit = t[-1]
    try:
        number = int(t[:-1])
    except ValueError:
        raise ValueError(f"Cannot parse tenor for sorting: {tenor!r}")
    if unit == "D":
        return number
    if unit in ("W", "Z"):
        return number * 7
    if unit == "M":
        return number * 30
    if unit == "Y":
        return number * 365
    raise ValueError(f"Cannot parse tenor for sorting: {tenor!r}")


def scale_quote(value: decimal.Decimal) -> decimal.Decimal:
    """Convert a raw Bloomberg OIS field value to the decimal convention used
    downstream: OIS quotes are in percent on Bloomberg (3.98 -> 0.0398)."""
    return value / decimal.Decimal(100)


@dataclass
class CurveQuote:
    tenor: str
    ticker: str
    value: decimal.Decimal
    field: str = "PX_LAST"
    source: str = "BBG"

    def __post_init__(self) -> None:
        self.tenor = normalize_tenor(self.tenor)

    def to_dict(self) -> dict:
        return {
            "tenor": self.tenor,
            "ticker": self.ticker,
            "value": _decimal_to_str(self.value),
            "field": self.field,
            "source": self.source,
        }

    @staticmethod
    def from_dict(data: dict) -> "CurveQuote":
        return CurveQuote(
            tenor=data["tenor"],
            ticker=data["ticker"],
            value=_str_to_decimal(data["value"]),
            field=data.get("field", "PX_LAST"),
            source=data.get("source", "BBG"),
        )


@dataclass
class Fixing:
    date: datetime.date
    value: decimal.Decimal

    def to_dict(self) -> dict:
        return {"date": _date_to_str(self.date), "value": _decimal_to_str(self.value)}

    @staticmethod
    def from_dict(data: dict) -> "Fixing":
        return Fixing(date=_str_to_date(data["date"]), value=_str_to_decimal(data["value"]))


@dataclass
class CurveSnapshot:
    currency: str
    index: str
    as_of: datetime.date
    quotes: List[CurveQuote]
    quote_time: Optional[datetime.datetime] = None

    def to_dict(self) -> dict:
        return {
            "currency": self.currency,
            "index": self.index,
            "as_of": _date_to_str(self.as_of),
            "quotes": [q.to_dict() for q in self.quotes],
        }

    @staticmethod
    def from_dict(data: dict) -> "CurveSnapshot":
        return CurveSnapshot(
            currency=data["currency"],
            index=data["index"],
            as_of=_str_to_date(data["as_of"]),
            quotes=[CurveQuote.from_dict(q) for q in data["quotes"]],
        )


@dataclass
class BbgCurvePoint:
    tenor: str
    rate: Optional[decimal.Decimal]

    def __post_init__(self) -> None:
        self.tenor = normalize_tenor(self.tenor)

    def to_dict(self) -> dict:
        return {"tenor": self.tenor, "rate": _decimal_to_str(self.rate)}

    @staticmethod
    def from_dict(data: dict) -> "BbgCurvePoint":
        return BbgCurvePoint(tenor=data["tenor"], rate=_str_to_decimal(data.get("rate")))


@dataclass
class BbgCurve:
    """Bloomberg's own built OIS curve (YCSW.... Index), for reconciliation only --
    never a source for our own bootstrapped curve, same role BBG_BDH plays for
    PAR_RATE/PV_USD/DV01_USD and BNP_BVAL plays for FX in CLAUDE.md."""

    currency: str
    index: str
    as_of: datetime.date
    curve_id: str
    points: List[BbgCurvePoint]

    def to_dict(self) -> dict:
        return {
            "currency": self.currency,
            "index": self.index,
            "as_of": _date_to_str(self.as_of),
            "curve_id": self.curve_id,
            "points": [p.to_dict() for p in self.points],
        }

    @staticmethod
    def from_dict(data: dict) -> "BbgCurve":
        return BbgCurve(
            currency=data["currency"],
            index=data["index"],
            as_of=_str_to_date(data["as_of"]),
            curve_id=data["curve_id"],
            points=[BbgCurvePoint.from_dict(p) for p in data["points"]],
        )


class MarketDataError(Exception):
    """Base error for this market data layer."""


class MarketDataUnavailable(MarketDataError):
    """Requested market data cannot be produced (missing tickers, wrong date,
    too few quotes)."""


# --------------------------------------------------------------------------- ticker map
# Ported from swapcalc/config/tickers.yaml `curves.<CCY>.<INDEX>`, OIS rows only.
# UNVERIFIED tags below are carried over unchanged from the reference project's yaml --
# none of these has been checked against a live terminal from this repo either.

@dataclass
class TickerSpec:
    tenor: str
    ticker: str
    field: str = "PX_LAST"

    def __post_init__(self) -> None:
        self.tenor = normalize_tenor(self.tenor)


OIS_CURVES: Dict[str, dict] = {
    "USD": {
        "index": "SOFR",
        "bbg_curve_id": "YCSW0490 Index",  # S490 - reasonable confidence
        "fixing_ticker": "SOFRRATE Index",
        "quotes": [
            TickerSpec("1W", "USOSFR1Z Curncy"),
            TickerSpec("2W", "USOSFR2Z Curncy"),
            TickerSpec("3W", "USOSFR3Z Curncy"),
            TickerSpec("1M", "USOSFRA Curncy"),
            TickerSpec("2M", "USOSFRB Curncy"),
            TickerSpec("3M", "USOSFRC Curncy"),
            TickerSpec("6M", "USOSFRF Curncy"),
            TickerSpec("9M", "USOSFRI Curncy"),
            TickerSpec("1Y", "USOSFR1 Curncy"),
            TickerSpec("2Y", "USOSFR2 Curncy"),
            TickerSpec("3Y", "USOSFR3 Curncy"),
            TickerSpec("5Y", "USOSFR5 Curncy"),
            TickerSpec("7Y", "USOSFR7 Curncy"),
            TickerSpec("10Y", "USOSFR10 Curncy"),
            TickerSpec("15Y", "USOSFR15 Curncy"),
            TickerSpec("20Y", "USOSFR20 Curncy"),
            TickerSpec("30Y", "USOSFR30 Curncy"),
        ],
    },
    "EUR": {
        "index": "ESTR",
        "bbg_curve_id": "YCSW0514 Index",  # UNVERIFIED
        "fixing_ticker": "ESTRON Index",  # UNVERIFIED
        "quotes": [
            TickerSpec("1W", "EESWE1Z Curncy"),  # UNVERIFIED
            TickerSpec("1M", "EESWEA Curncy"),  # UNVERIFIED
            TickerSpec("3M", "EESWEC Curncy"),  # UNVERIFIED
            TickerSpec("6M", "EESWEF Curncy"),  # UNVERIFIED
            TickerSpec("1Y", "EESWE1 Curncy"),  # UNVERIFIED
            TickerSpec("2Y", "EESWE2 Curncy"),  # UNVERIFIED
            TickerSpec("3Y", "EESWE3 Curncy"),  # UNVERIFIED
            TickerSpec("5Y", "EESWE5 Curncy"),  # UNVERIFIED
            TickerSpec("7Y", "EESWE7 Curncy"),  # UNVERIFIED
            TickerSpec("10Y", "EESWE10 Curncy"),  # UNVERIFIED
            TickerSpec("20Y", "EESWE20 Curncy"),  # UNVERIFIED
            TickerSpec("30Y", "EESWE30 Curncy"),  # UNVERIFIED
        ],
    },
    "GBP": {
        "index": "SONIA",
        "bbg_curve_id": "YCSW0141 Index",  # UNVERIFIED
        "fixing_ticker": "SONIO/N Index",  # UNVERIFIED
        "quotes": [
            TickerSpec("1W", "BPSWS1Z Curncy"),  # UNVERIFIED
            TickerSpec("1M", "BPSWSA Curncy"),  # UNVERIFIED
            TickerSpec("3M", "BPSWSC Curncy"),  # UNVERIFIED
            TickerSpec("6M", "BPSWSF Curncy"),  # UNVERIFIED
            TickerSpec("1Y", "BPSWS1 Curncy"),  # UNVERIFIED
            TickerSpec("2Y", "BPSWS2 Curncy"),  # UNVERIFIED
            TickerSpec("5Y", "BPSWS5 Curncy"),  # UNVERIFIED
            TickerSpec("10Y", "BPSWS10 Curncy"),  # UNVERIFIED
            TickerSpec("30Y", "BPSWS30 Curncy"),  # UNVERIFIED
        ],
    },
    "JPY": {
        "index": "TONA",
        "bbg_curve_id": "YCSW0195 Index",  # UNVERIFIED
        "fixing_ticker": "MUTKCALM Index",  # UNVERIFIED
        "quotes": [
            TickerSpec("1W", "JYSO1Z Curncy"),  # UNVERIFIED
            TickerSpec("1M", "JYSOA Curncy"),  # UNVERIFIED
            TickerSpec("3M", "JYSOC Curncy"),  # UNVERIFIED
            TickerSpec("6M", "JYSOF Curncy"),  # UNVERIFIED
            TickerSpec("1Y", "JYSO1 Curncy"),  # UNVERIFIED
            TickerSpec("2Y", "JYSO2 Curncy"),  # UNVERIFIED
            TickerSpec("5Y", "JYSO5 Curncy"),  # UNVERIFIED
            TickerSpec("10Y", "JYSO10 Curncy"),  # UNVERIFIED
            TickerSpec("30Y", "JYSO30 Curncy"),  # UNVERIFIED
        ],
    },
    "CHF": {
        "index": "SARON",
        "bbg_curve_id": "YCSW0234 Index",  # UNVERIFIED
        "fixing_ticker": "SSARON Index",  # UNVERIFIED
        "quotes": [
            TickerSpec("1W", "SFSNT1Z Curncy"),  # UNVERIFIED
            TickerSpec("1M", "SFSNTA Curncy"),  # UNVERIFIED
            TickerSpec("3M", "SFSNTC Curncy"),  # UNVERIFIED
            TickerSpec("6M", "SFSNTF Curncy"),  # UNVERIFIED
            TickerSpec("1Y", "SFSNT1 Curncy"),  # UNVERIFIED
            TickerSpec("5Y", "SFSNT5 Curncy"),  # UNVERIFIED
            TickerSpec("10Y", "SFSNT10 Curncy"),  # UNVERIFIED
            TickerSpec("30Y", "SFSNT30 Curncy"),  # UNVERIFIED
        ],
    },
    "CAD": {
        "index": "CORRA",
        "bbg_curve_id": "YCSW0147 Index",  # UNVERIFIED
        "fixing_ticker": "CAONREPO Index",  # UNVERIFIED
        "quotes": [
            TickerSpec("1W", "CDSO1Z Curncy"),  # UNVERIFIED
            TickerSpec("1M", "CDSOA Curncy"),  # UNVERIFIED
            TickerSpec("3M", "CDSOC Curncy"),  # UNVERIFIED
            TickerSpec("6M", "CDSOF Curncy"),  # UNVERIFIED
            TickerSpec("1Y", "CDSO1 Curncy"),  # UNVERIFIED
            TickerSpec("5Y", "CDSO5 Curncy"),  # UNVERIFIED
            TickerSpec("10Y", "CDSO10 Curncy"),  # UNVERIFIED
            TickerSpec("30Y", "CDSO30 Curncy"),  # UNVERIFIED
        ],
    },
    "AUD": {
        "index": "AONIA",
        "bbg_curve_id": "YCSW0305 Index",  # UNVERIFIED
        "fixing_ticker": "RBACOR Index",  # UNVERIFIED
        "quotes": [
            TickerSpec("1W", "ADSO1Z Curncy"),  # UNVERIFIED
            TickerSpec("1M", "ADSOA Curncy"),  # UNVERIFIED
            TickerSpec("3M", "ADSOC Curncy"),  # UNVERIFIED
            TickerSpec("6M", "ADSOF Curncy"),  # UNVERIFIED
            TickerSpec("1Y", "ADSO1 Curncy"),  # UNVERIFIED
            TickerSpec("5Y", "ADSO5 Curncy"),  # UNVERIFIED
            TickerSpec("10Y", "ADSO10 Curncy"),  # UNVERIFIED
            TickerSpec("30Y", "ADSO30 Curncy"),  # UNVERIFIED
        ],
    },
}


class TickerMapError(Exception):
    pass


def ois_curve(currency: str) -> List[TickerSpec]:
    entry = OIS_CURVES.get(currency.upper())
    if entry is None:
        raise TickerMapError(
            f"No OIS curve for {currency}. Available: {', '.join(sorted(OIS_CURVES))}"
        )
    return entry["quotes"]


def ois_bbg_curve_id(currency: str) -> Optional[str]:
    entry = OIS_CURVES.get(currency.upper())
    return entry.get("bbg_curve_id") if entry else None


def ois_fixing_ticker(currency: str) -> str:
    entry = OIS_CURVES.get(currency.upper())
    if entry is None:
        raise TickerMapError(
            f"No OIS fixing ticker for {currency}. Available: {', '.join(sorted(OIS_CURVES))}"
        )
    return entry["fixing_ticker"]


# --------------------------------------------------------------------------- wire format
# build_refdata_spec/build_histdata_spec are pure (no blpapi dependency) and
# unit-testable without the SDK installed, ported from swapcalc/marketdata/bloomberg.py.
# The actual request/response wire handling on RatesBloombergSource below is written
# directly against named-element access (hasElement/getElement(name)/getValue(),
# numValues()/getValueAsElement(i)) rather than the reference project's generic
# isArray()/numElements()/name() element walk, to match this repo's blpapi field
# access pattern (see data/bloomberg/pull_marks.py fetch_reference/fetch_historical,
# which the fake blpapi test harness in tests/test_bloomberg.py is built around).

def build_refdata_spec(
    tickers: List[str],
    fields: List[str],
    overrides: Optional[Dict[str, str]] = None,
    chunk: int = 100,
) -> List[dict]:
    specs = []
    overrides = dict(overrides) if overrides else {}
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        specs.append({
            "request_type": "ReferenceDataRequest",
            "securities": list(batch),
            "fields": list(fields),
            "overrides": overrides,
        })
    return specs


def build_histdata_spec(
    tickers: List[str],
    fields: List[str],
    start: datetime.date,
    end: datetime.date,
    non_trading_day_fill: bool = False,
) -> dict:
    spec = {
        "request_type": "HistoricalDataRequest",
        "securities": list(tickers),
        "fields": list(fields),
        "startDate": start.isoformat().replace("-", ""),
        "endDate": end.isoformat().replace("-", ""),
        "periodicitySelection": "DAILY",
    }
    if non_trading_day_fill:
        spec["nonTradingDayFillOption"] = "NON_TRADING_WEEKDAYS"
        spec["nonTradingDayFillMethod"] = "PREVIOUS_VALUE"
    return spec


class RatesBloombergSource:
    """Live blpapi wrapper for OIS curve quotes, fixings and Bloomberg's own
    reconciliation curve. Opens its own blpapi.Session -- see module docstring."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8194,
        timeout_ms: int = 30000,
    ) -> None:
        try:
            import blpapi  # noqa: F401
        except ImportError as exc:
            raise MarketDataError(
                "blpapi not installed. Install the Bloomberg Python API on the terminal "
                "machine (see WAPI <GO>), or use RatesFileSource with a saved snapshot."
            ) from exc

        self._blpapi = blpapi
        self.host = host
        self.port = port
        self.timeout_ms = timeout_ms

        session_options = blpapi.SessionOptions()
        session_options.setServerHost(host)
        session_options.setServerPort(port)
        # Own session: deliberately not shared with pull_marks.py's session -- see
        # module docstring. Follow-up to merge is tracked by the housekeeper in
        # docs/open-questions.md, not addressed here.
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
        return f"RatesBloombergSource({self.host}:{self.port})"

    def close(self) -> None:
        try:
            self._session.stop()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "RatesBloombergSource":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # -- internal: request/response, named-element access (see note above) --------------

    def _fetch_reference(
        self, tickers: List[str], fields: List[str], overrides: Optional[Dict[str, str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """ReferenceDataRequest for `tickers`/`fields` (live snapshot). Returns
        {ticker: {field: raw value}}; a field absent from the response for a
        given ticker is simply missing from that ticker's dict."""
        blpapi = self._blpapi
        request = self._service.createRequest("ReferenceDataRequest")
        for t in tickers:
            request.getElement("securities").appendValue(t)
        for f in fields:
            request.getElement("fields").appendValue(f)
        if overrides:
            ov = request.getElement("overrides")
            for name, value in overrides.items():
                o = ov.appendElement()
                o.setElement("fieldId", name)
                o.setElement("value", value)

        logger.debug("Sending ReferenceDataRequest: %d securities, fields=%s", len(tickers), fields)
        self._session.sendRequest(request)

        out: Dict[str, Dict[str, Any]] = {}
        consecutive_timeouts = 0
        while True:
            event = self._session.nextEvent(self.timeout_ms)
            event_type = event.eventType()
            if event_type == blpapi.Event.TIMEOUT:
                consecutive_timeouts += 1
                if consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
                    raise MarketDataUnavailable(
                        f"Bloomberg ReferenceDataRequest on {self.host}:{self.port} timed out "
                        f"after {consecutive_timeouts} consecutive waits."
                    )
                continue
            consecutive_timeouts = 0
            event_is_ours = False
            for msg in event:
                if not msg.hasElement("securityData"):
                    continue
                event_is_ours = True
                sec_data = msg.getElement("securityData")
                for i in range(sec_data.numValues()):
                    sd = sec_data.getValueAsElement(i)
                    ticker = sd.getElementAsString("security")
                    row: Dict[str, Any] = {}
                    if sd.hasElement("fieldData"):
                        fd = sd.getElement("fieldData")
                        for f in fields:
                            if fd.hasElement(f):
                                row[f] = fd.getElement(f).getValue()
                    out[ticker] = row
            if event_type == blpapi.Event.RESPONSE and event_is_ours:
                break
        return out

    def _fetch_historical_single(self, tickers: List[str], field: str, as_of: datetime.date) -> Dict[str, Any]:
        """HistoricalDataRequest for a single date (start = end = as_of), batched over
        `tickers`. Returns {ticker: raw value or None}."""
        blpapi = self._blpapi
        request = self._service.createRequest("HistoricalDataRequest")
        for t in tickers:
            request.getElement("securities").appendValue(t)
        request.getElement("fields").appendValue(field)
        d = as_of.strftime("%Y%m%d")
        request.set("startDate", d)
        request.set("endDate", d)

        logger.debug("Sending HistoricalDataRequest: %d securities, field=%s, date=%s", len(tickers), field, d)
        self._session.sendRequest(request)

        out: Dict[str, Any] = {}
        consecutive_timeouts = 0
        while True:
            event = self._session.nextEvent(self.timeout_ms)
            event_type = event.eventType()
            if event_type == blpapi.Event.TIMEOUT:
                consecutive_timeouts += 1
                if consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
                    raise MarketDataUnavailable(
                        f"Bloomberg HistoricalDataRequest on {self.host}:{self.port} timed out "
                        f"after {consecutive_timeouts} consecutive waits."
                    )
                continue
            consecutive_timeouts = 0
            event_is_ours = False
            for msg in event:
                if not msg.hasElement("securityData"):
                    continue
                event_is_ours = True
                sec_data = msg.getElement("securityData")
                ticker = sec_data.getElementAsString("security")
                value = None
                if sec_data.hasElement("fieldData"):
                    fd = sec_data.getElement("fieldData")
                    if fd.numValues() > 0:
                        point = fd.getValueAsElement(0)
                        if point.hasElement(field):
                            value = point.getElement(field).getValue()
                out[ticker] = value
            if event_type == blpapi.Event.RESPONSE and event_is_ours:
                break
        return out

    def _fetch_historical_series(self, ticker: str, field: str, start: datetime.date, end: datetime.date) -> List[tuple]:
        """HistoricalDataRequest for [start, end] on a single ticker. Returns a list of
        (raw date, raw value) pairs, one per publication day in the response."""
        blpapi = self._blpapi
        request = self._service.createRequest("HistoricalDataRequest")
        request.getElement("securities").appendValue(ticker)
        request.getElement("fields").appendValue(field)
        request.set("startDate", start.strftime("%Y%m%d"))
        request.set("endDate", end.strftime("%Y%m%d"))

        logger.debug("Sending HistoricalDataRequest: %s, field=%s, %s to %s", ticker, field, start, end)
        self._session.sendRequest(request)

        points: List[tuple] = []
        consecutive_timeouts = 0
        while True:
            event = self._session.nextEvent(self.timeout_ms)
            event_type = event.eventType()
            if event_type == blpapi.Event.TIMEOUT:
                consecutive_timeouts += 1
                if consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
                    raise MarketDataUnavailable(
                        f"Bloomberg HistoricalDataRequest on {self.host}:{self.port} timed out "
                        f"after {consecutive_timeouts} consecutive waits."
                    )
                continue
            consecutive_timeouts = 0
            event_is_ours = False
            for msg in event:
                if not msg.hasElement("securityData"):
                    continue
                event_is_ours = True
                sec_data = msg.getElement("securityData")
                if sec_data.hasElement("fieldData"):
                    fd = sec_data.getElement("fieldData")
                    for i in range(fd.numValues()):
                        point = fd.getValueAsElement(i)
                        if point.hasElement(field):
                            d = point.getElement("date").getValue() if point.hasElement("date") else None
                            v = point.getElement(field).getValue()
                            points.append((d, v))
            if event_type == blpapi.Event.RESPONSE and event_is_ours:
                break
        return points

    # -- public API -----------------------------------------------------------------------

    def get_curve_quotes(self, currency: str, as_of: datetime.date) -> CurveSnapshot:
        specs = ois_curve(currency)
        ticker_to_spec = {s.ticker: s for s in specs}
        tickers = list(ticker_to_spec.keys())
        # OIS curve quotes here are all PX_LAST (see OIS_CURVES) -- a single field.
        field = "PX_LAST"

        if as_of == datetime.date.today():
            values = self._fetch_reference(tickers, [field])
        else:
            raw = self._fetch_historical_single(tickers, field, as_of)
            values = {t: ({field: v} if v is not None else {}) for t, v in raw.items()}

        quotes = []
        for ticker, spec in ticker_to_spec.items():
            field_values = values.get(ticker)
            raw_value = field_values.get(spec.field) if field_values else None
            if raw_value is None:
                logger.warning("Dropping OIS quote %s tenor %s: no value for field %s", currency, spec.tenor, spec.field)
                continue
            quotes.append(CurveQuote(
                tenor=spec.tenor, ticker=ticker,
                value=scale_quote(decimal.Decimal(str(raw_value))), field=spec.field,
            ))
        quotes.sort(key=lambda q: tenor_to_days(q.tenor))
        if len(quotes) < _MIN_QUOTES:
            failed = [t for t in ticker_to_spec if not values.get(t, {}).get(field)]
            raise MarketDataUnavailable(
                f"Fewer than {_MIN_QUOTES} OIS quotes available for {currency}. Failed tickers: {', '.join(failed)}"
            )
        index = OIS_CURVES[currency.upper()]["index"]
        return CurveSnapshot(currency=currency.upper(), index=index, as_of=as_of, quotes=quotes)

    def get_fixings(self, currency: str, start: datetime.date, end: datetime.date) -> List[Fixing]:
        ticker = ois_fixing_ticker(currency)
        logger.debug("Fetching OIS fixings for %s: %s to %s", currency, start, end)
        points = self._fetch_historical_series(ticker, "PX_LAST", start, end)
        fixings = []
        for date_value, px in points:
            if date_value is None or px is None:
                continue
            if isinstance(date_value, datetime.date):
                d = date_value
            elif hasattr(date_value, "date"):
                d = date_value.date()
            else:
                d = datetime.date.fromisoformat(str(date_value))
            fixings.append(Fixing(date=d, value=scale_quote(decimal.Decimal(str(px)))))
        fixings.sort(key=lambda f: f.date)
        return fixings

    def get_bbg_curve(self, currency: str, as_of: datetime.date) -> Optional[BbgCurve]:
        """Bloomberg's own OIS curve, for reconciliation only. Never raises --
        returns None if the curve is unavailable."""
        curve_id = ois_bbg_curve_id(currency)
        if not curve_id:
            return None
        index = OIS_CURVES.get(currency.upper(), {}).get("index", "")
        try:
            values = self._fetch_reference([curve_id], ["CURVE_TENOR_RATES"])
            rows = values.get(curve_id, {}).get("CURVE_TENOR_RATES")
            points = self._parse_bbg_curve_bulk(rows)
            if points:
                return BbgCurve(currency=currency.upper(), index=index, as_of=as_of, curve_id=curve_id, points=points)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CURVE_TENOR_RATES failed for %s: %s", curve_id, exc)
        logger.warning("No Bloomberg OIS curve available for %s (%s)", currency, curve_id)
        return None

    @staticmethod
    def _parse_bbg_curve_bulk(rows: Optional[List[dict]]) -> List[BbgCurvePoint]:
        points = []
        if not rows:
            return points
        for row in rows:
            tenor = row.get("tenor") or row.get("Tenor")
            rate = row.get("rate") or row.get("Rate")
            if tenor is None:
                continue
            points.append(BbgCurvePoint(
                tenor=str(tenor),
                rate=decimal.Decimal(str(rate)) / decimal.Decimal(100) if rate is not None else None,
            ))
        return points


# --------------------------------------------------------------------------- FileSource
# Mirrors swapcalc/marketdata/filesource.py's pattern: no blpapi dependency at all, reads
# a saved JSON snapshot. Used by tests and offline work.

@dataclass
class OisSnapshot:
    as_of: datetime.date
    source: str
    curves: Dict[str, CurveSnapshot] = field(default_factory=dict)
    fixings: Dict[str, List[Fixing]] = field(default_factory=dict)
    bbg_curves: Dict[str, BbgCurve] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "as_of": _date_to_str(self.as_of),
            "source": self.source,
            "curves": {k: v.to_dict() for k, v in self.curves.items()},
            "fixings": {k: [f.to_dict() for f in v] for k, v in self.fixings.items()},
            "bbg_curves": {k: v.to_dict() for k, v in self.bbg_curves.items()},
        }

    @staticmethod
    def from_dict(data: dict) -> "OisSnapshot":
        return OisSnapshot(
            as_of=_str_to_date(data["as_of"]),
            source=data["source"],
            curves={k: CurveSnapshot.from_dict(v) for k, v in data.get("curves", {}).items()},
            fixings={k: [Fixing.from_dict(f) for f in v] for k, v in data.get("fixings", {}).items()},
            bbg_curves={k: BbgCurve.from_dict(v) for k, v in data.get("bbg_curves", {}).items()},
        )


def load_ois_snapshot(path: Union[str, Path]) -> OisSnapshot:
    with open(path, "r", encoding="utf-8") as f:
        return OisSnapshot.from_dict(json.load(f))


def save_ois_snapshot(snap: OisSnapshot, path: Union[str, Path]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


class RatesFileSource:
    """FileSource-equivalent for OIS curves: never touches blpapi, reads a saved
    OisSnapshot (JSON or already-loaded)."""

    def __init__(self, snapshot_or_path: Union[OisSnapshot, str, Path]) -> None:
        if isinstance(snapshot_or_path, OisSnapshot):
            self._snapshot = snapshot_or_path
        else:
            self._snapshot = load_ois_snapshot(snapshot_or_path)

    def name(self) -> str:
        return f"RatesFileSource({self._snapshot.source})"

    def get_curve_quotes(self, currency: str, as_of: datetime.date) -> CurveSnapshot:
        if as_of != self._snapshot.as_of:
            raise MarketDataUnavailable(
                f"Snapshot is as of {self._snapshot.as_of}, requested {as_of} for {currency}"
            )
        curve = self._snapshot.curves.get(currency.upper())
        if curve is None:
            raise MarketDataUnavailable(
                f"No OIS curve for {currency} in snapshot. Available: {', '.join(sorted(self._snapshot.curves))}"
            )
        return curve

    def get_fixings(self, currency: str, start: datetime.date, end: datetime.date) -> List[Fixing]:
        history = self._snapshot.fixings.get(currency.upper())
        if history is None:
            raise MarketDataUnavailable(
                f"No OIS fixings for {currency} in snapshot. Available: {', '.join(sorted(self._snapshot.fixings))}"
            )
        return [f for f in history if start <= f.date <= end]

    def get_bbg_curve(self, currency: str, as_of: datetime.date) -> Optional[BbgCurve]:
        return self._snapshot.bbg_curves.get(currency.upper())


# --------------------------------------------------------------------------- curve_quotes
# Staging table owned by data/ingest/schema.py (added there in a parallel task). Write
# defensively: CREATE TABLE IF NOT EXISTS here so this module works before or after that
# migration lands, using the exact column set specified for this task.

CURVE_QUOTES_COLUMNS = ["as_of_date", "ccy", "index", "tenor", "ticker", "value", "quote_type", "field", "source"]

_CURVE_QUOTES_DDL = """
CREATE TABLE IF NOT EXISTS curve_quotes (
  as_of_date  TEXT NOT NULL,
  ccy         TEXT NOT NULL,
  "index"     TEXT NOT NULL,
  tenor       TEXT NOT NULL,
  ticker      TEXT NOT NULL,
  value       REAL NOT NULL,
  quote_type  TEXT NOT NULL,
  field       TEXT NOT NULL,
  source      TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, "index", tenor, source)
);
"""


def ensure_curve_quotes_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CURVE_QUOTES_DDL)


def write_curve_quotes(
    conn: sqlite3.Connection,
    snapshot: CurveSnapshot,
    as_of_date: str,
    source: str = "BBG_BDP",
    quote_type: str = "OIS",
) -> int:
    """Insert one row per quote in ``snapshot`` into curve_quotes (creating the
    table defensively if it does not exist yet -- see module docstring).

    Uses INSERT OR REPLACE keyed on (as_of_date, ccy, index, tenor, source), so
    re-running a pull for the same day/source updates rather than duplicate-key
    errors. Returns the number of rows written.
    """
    ensure_curve_quotes_table(conn)
    rows = [
        (as_of_date, snapshot.currency, snapshot.index, q.tenor, q.ticker, float(q.value), quote_type, q.field, source)
        for q in snapshot.quotes
    ]
    with conn:
        conn.executemany(
            'INSERT OR REPLACE INTO curve_quotes (as_of_date, ccy, "index", tenor, ticker, value, quote_type, field, source) '
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)
