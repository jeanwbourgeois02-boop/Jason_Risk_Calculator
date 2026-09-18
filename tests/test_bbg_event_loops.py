"""Bloomberg event-loop behaviour shared by data/bloomberg/rates_marketdata.py and
data/bloomberg/vol_marketdata.py (2026-09-18 audit): a whole-request `responseError` is
reported immediately with Bloomberg's own text (not after three 30 s timeouts as
"timed out"), a per-security `securityError` leaves that ticker empty without hanging,
a message tagged with another request's CorrelationId is ignored, and the vol feed's
request-shape assumption is only credited by a real per-security answer. Minimal fake
blpapi module; no Terminal needed."""
import datetime
import sys
import types

import pytest


class _El:
    def __init__(self, value):
        self._v = value

    def hasElement(self, name):
        return isinstance(self._v, dict) and name in self._v

    def getElement(self, name):
        return _El(self._v[name])

    def getElementAsString(self, name):
        return str(self._v[name])

    def numValues(self):
        return len(self._v)

    def getValueAsElement(self, i):
        return _El(self._v[i])

    def getValue(self):
        return self._v

    def __str__(self):
        return str(self._v)


class _Cid:
    def __init__(self, v):
        self.v = v

    def __eq__(self, other):
        return isinstance(other, _Cid) and other.v == self.v

    def __hash__(self):
        return hash(self.v)


class _Msg:
    def __init__(self, data, cid):
        self._d, self._cid = data, cid

    def hasElement(self, name):
        return name in self._d

    def getElement(self, name):
        return _El(self._d[name])

    def correlationIds(self):
        return [self._cid] if self._cid is not None else []


class _Event:
    def __init__(self, msgs, kind):
        self._m, self._k = msgs, kind

    def __iter__(self):
        return iter(self._m)

    def eventType(self):
        return self._k


class _Override:
    def setElement(self, k, v):
        setattr(self, k, v)


class _Appender:
    def __init__(self):
        self.items = []

    def appendValue(self, v):
        self.items.append(v)

    def appendElement(self):
        o = _Override()
        self.items.append(o)
        return o


class _Req:
    def __init__(self, t):
        self.t, self.lists = t, {}

    def getElement(self, n):
        return self.lists.setdefault(n, _Appender())

    def set(self, n, v):
        setattr(self, n, v)


class _Session:
    """`script`: list of (messages_as_dicts, event_type, cid) served in order by nextEvent;
    cid "own" means the CorrelationId the request under test was sent with, None means an
    untagged (session/service status) message. An empty script yields TIMEOUT events."""

    def __init__(self, opts=None):
        self.script, self.next_calls, self.sent, self._cid = [], 0, [], None

    def start(self):
        return True

    def openService(self, n):
        return True

    def getService(self, n):
        return self

    def createRequest(self, t):
        return _Req(t)

    def sendRequest(self, request, correlationId=None):
        self.sent.append((request, correlationId))
        self._cid = correlationId

    def nextEvent(self, timeout=None):
        self.next_calls += 1
        if not self.script:
            return _Event([], "TIMEOUT")
        data, kind, cid = self.script.pop(0)
        cid = self._cid if cid == "own" else cid
        return _Event([_Msg(d, cid) for d in data], kind)

    def stop(self):
        pass


@pytest.fixture
def fake_blpapi(monkeypatch):
    mod = types.ModuleType("blpapi")
    mod.SessionOptions = lambda: types.SimpleNamespace(setServerHost=lambda h: None, setServerPort=lambda p: None)
    mod.Session = _Session
    mod.Event = types.SimpleNamespace(RESPONSE="RESPONSE", TIMEOUT="TIMEOUT", PARTIAL_RESPONSE="PARTIAL_RESPONSE")
    mod.CorrelationId = _Cid
    monkeypatch.setitem(sys.modules, "blpapi", mod)
    return mod


def _rates_source():
    from data.bloomberg.rates_marketdata import RatesBloombergSource
    src = RatesBloombergSource(host="localhost", port=8194, timeout_ms=10)
    return src, src._session


# --------------------------------------------------------------------------- rates feed
def test_rates_response_error_is_reported_immediately_with_bloombergs_text(fake_blpapi):
    from data.bloomberg.rates_marketdata import MarketDataUnavailable
    src, session = _rates_source()
    session.script = [([{"responseError": {"message": "Not entitled to field PX_LAST"}}], "RESPONSE", "own")]
    with pytest.raises(MarketDataUnavailable) as exc:
        src._fetch_reference(["USOSFR1 Curncy"], ["PX_LAST"])
    assert "Not entitled" in str(exc.value) and "timed out" not in str(exc.value)
    assert session.next_calls == 1          # no three-timeout wait


def test_rates_security_error_leaves_the_ticker_empty_and_returns(fake_blpapi):
    src, session = _rates_source()
    session.script = [([{"securityData": [
        {"security": "USOSFR1 Curncy", "fieldData": {"PX_LAST": 3.98}},
        {"security": "BADTICKER Curncy", "securityError": {"message": "Unknown/Invalid Security"}},
    ]}], "RESPONSE", "own")]
    out = src._fetch_reference(["USOSFR1 Curncy", "BADTICKER Curncy"], ["PX_LAST"])
    assert out == {"USOSFR1 Curncy": {"PX_LAST": 3.98}, "BADTICKER Curncy": {}}
    assert session.next_calls == 1


def test_rates_stale_message_from_a_previous_request_is_ignored(fake_blpapi):
    src, session = _rates_source()
    session.script = [
        ([{"securityData": [{"security": "USOSFR1 Curncy", "fieldData": {"PX_LAST": 1.0}}]}], "RESPONSE", _Cid("old")),
        ([{"securityData": [{"security": "USOSFR1 Curncy", "fieldData": {"PX_LAST": 3.98}}]}], "RESPONSE", "own"),
    ]
    out = src._fetch_reference(["USOSFR1 Curncy"], ["PX_LAST"])
    assert out["USOSFR1 Curncy"]["PX_LAST"] == 3.98
    assert session.sent[-1][1] is not None   # sent with its own CorrelationId


def test_rates_untagged_status_message_then_partial_then_final_response(fake_blpapi):
    src, session = _rates_source()
    session.script = [
        ([{"sessionStatus": {}}], "SESSION_STATUS", None),
        ([{"securityData": [{"security": "USOSFR1 Curncy", "fieldData": {"PX_LAST": 3.98}}]}], "PARTIAL_RESPONSE", "own"),
        ([{"securityData": [{"security": "USOSFR2 Curncy", "fieldData": {"PX_LAST": 3.90}}]}], "RESPONSE", "own"),
    ]
    out = src._fetch_reference(["USOSFR1 Curncy", "USOSFR2 Curncy"], ["PX_LAST"])
    assert out == {"USOSFR1 Curncy": {"PX_LAST": 3.98}, "USOSFR2 Curncy": {"PX_LAST": 3.90}}


def test_rates_historical_response_error_is_immediate(fake_blpapi):
    from data.bloomberg.rates_marketdata import MarketDataUnavailable
    src, session = _rates_source()
    session.script = [([{"responseError": {"message": "Daily limit reached"}}], "RESPONSE", "own")]
    with pytest.raises(MarketDataUnavailable, match="Daily limit"):
        src._fetch_historical_single(["SOFRRATE Index"], "PX_LAST", datetime.date(2026, 9, 17))
    assert session.next_calls == 1


def test_rates_still_times_out_after_three_empty_waits(fake_blpapi):
    from data.bloomberg.rates_marketdata import MarketDataUnavailable
    src, session = _rates_source()
    with pytest.raises(MarketDataUnavailable, match="timed out after 3 consecutive waits"):
        src._fetch_reference(["USOSFR1 Curncy"], ["PX_LAST"])
    assert session.next_calls == 3


# --------------------------------------------------------------------------- vol feed
def test_vol_response_error_is_the_batch_detail_not_a_timeout(fake_blpapi):
    from data.bloomberg.vol_marketdata import VolBloombergSource
    src = VolBloombergSource(host="localhost", port=8194, timeout_ms=10)
    session = src._session
    session.script = [([{"responseError": {"message": "Invalid override"}}], "RESPONSE", "own")]
    out, detail = src._fetch(["EURUSDV1M BGN Curncy"], None)
    assert out == {} and "Invalid override" in detail and "timed out" not in detail
    assert session.next_calls == 1


def test_vol_request_type_assumption_needs_a_real_per_security_answer():
    from data.bloomberg.vol_marketdata import VolFetchResult, assess_ticker_assumptions
    day = datetime.date(2026, 9, 17)
    all_missing = VolFetchResult(as_of=day, source="BBG_BDP", pairs={}, diagnostics=[
        {"pair": "EURUSD", "tenor": "1M", "quote_type": "ATM", "ticker": "EURUSDV1M BGN Curncy",
         "status": "MISSING", "bbg_status": "NO_VALUE", "detail": "nextEvent timed out after 3 consecutive waits"}])
    assert "request_type" not in {r["assumption_id"] for r in assess_ticker_assumptions(all_missing)}
    rejected = VolFetchResult(as_of=day, source="BBG_BDP", pairs={}, diagnostics=[
        {"pair": "EURUSD", "tenor": "ON", "quote_type": "ATM", "ticker": "EURUSDVON BGN Curncy",
         "status": "MISSING", "bbg_status": "SECURITY_ERROR", "detail": "Unknown/Invalid Security"}])
    rows = {r["assumption_id"]: r for r in assess_ticker_assumptions(rejected)}
    assert rows["request_type"]["outcome"] == "OK" and rows["on_tenor"]["outcome"] == "SECURITY_ERROR"
