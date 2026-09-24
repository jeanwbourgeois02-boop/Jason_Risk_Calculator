"""data/bloomberg/ticker_check.py: the Bloomberg ticker check run at the terminal (user,
2026-09-24: "add a bloomberg diagnostic tool I will be able to use so that I can diagnose when I
finally have access to bloomberg"). A fake client returns canned field values; one test drives
the real BlpapiClient through a minimal fake blpapi module. No terminal needed."""
import csv
import dataclasses
import sys
import types
from datetime import date, datetime

import pytest

from data.bloomberg import ticker_check as tc
from data.contracts import load_roots
from engine import lme as _lme

NOW = datetime(2026, 9, 24, 10, 0, 0)
AS_OF = date(2026, 9, 24)


def _root(**kw):
    """A ContractRoot built from any row of config/contracts.csv, so the tests do not depend on
    which roots the file holds or on its current guesses."""
    base = next(iter(load_roots().values()))
    defaults = dict(sector="energy", subsector="", country="US", bbg_yellow_key="Comdty", bbg_verified=False,
                    active_months=tuple(range(1, 13)), delivery="physical", status="active", notes="",
                    option_style="", option_lead_months=None)
    defaults.update(kw)
    return dataclasses.replace(base, **defaults)


CL = _root(root_id="NYMEX:CL", name="NYMEX WTI crude oil", exchange="NYMEX", exchange_code="CL", bbg_root="CL",
           currency="USD", contract_size=1000.0, size_unit="bbl", quote_unit="USD/bbl", price_scale=1.0,
           multiplier=1000.0, calendar="US")
# Corn with the scale wrong on purpose: Bloomberg quotes cents, our row says dollars.
CORN_BAD = _root(root_id="CBOT:ZC", name="CBOT corn", sector="agriculture", exchange="CBOT", exchange_code="ZC",
                 bbg_root="C", currency="USD", contract_size=5000.0, size_unit="bu", quote_unit="USD/bu",
                 price_scale=1.0, multiplier=5000.0, calendar="US")
NBP = _root(root_id="ICE:M", name="ICE UK NBP natural gas", exchange="ICE", exchange_code="M", bbg_root="FN",
            currency="GBP", contract_size=30000.0, size_unit="therm", quote_unit="GBP/therm", price_scale=0.01,
            multiplier=300.0, calendar="ICE_EU", country="GB")
NBP_BAD = dataclasses.replace(NBP, price_scale=1.0, multiplier=30000.0)
BAD = _root(root_id="DCE:J", name="DCE coke", sector="ferrous", exchange="DCE", exchange_code="J", bbg_root="KEE",
            currency="CNY", contract_size=100.0, size_unit="t", quote_unit="CNY/t", price_scale=1.0,
            multiplier=100.0, calendar="CN", country="CN")
DEAD = _root(root_id="SHFE:WR", name="SHFE wire rod", sector="ferrous", exchange="SHFE", exchange_code="WR",
             bbg_root="WIR", currency="CNY", contract_size=10.0, size_unit="t", quote_unit="CNY/t", price_scale=1.0,
             multiplier=10.0, calendar="CN", country="CN")
PLACEHOLDER = _root(root_id="SHFE:SS", name="SHFE stainless steel", sector="ferrous", exchange="SHFE",
                    exchange_code="SS", bbg_root="ZZSS", currency="CNY", contract_size=5.0, size_unit="t",
                    quote_unit="CNY/t", price_scale=1.0, multiplier=5.0, calendar="CN", country="CN")
CU = _root(root_id="SHFE:CU", name="SHFE copper cathode", sector="metals", exchange="SHFE", exchange_code="CU",
           bbg_root="CU", currency="CNY", contract_size=5.0, size_unit="t", quote_unit="CNY/t", price_scale=1.0,
           multiplier=5.0, calendar="CN", country="CN")

CANNED = {
    "CL1 Comdty": {"NAME": "Generic 1st 'CL' Future", "SECURITY_DES": "CL1", "EXCH_CODE": "NYM", "CRNCY": "USD",
                   "FUT_CONT_SIZE": 1000.0, "FUT_TRADING_UNITS": "Barrels", "FUT_VAL_PT": 1000.0,
                   "FUT_TICK_SIZE": 0.01, "FUT_TICK_VAL": 10.0, "FUT_CUR_GEN_TICKER": "CLX6", "PX_LAST": 72.15},
    "C 1 Comdty": {"NAME": "Generic 1st 'C ' Future", "EXCH_CODE": "CBT", "CRNCY": "USd", "FUT_CONT_SIZE": 5000.0,
                   "FUT_VAL_PT": 50.0, "FUT_CUR_GEN_TICKER": "C Z6", "PX_LAST": 452.25},
    "FN1 Comdty": {"NAME": "UK NBP NATURAL GAS FUTR", "EXCH_CODE": "ICE", "CRNCY": "GBp", "FUT_VAL_PT": 300.0,
                   "PX_LAST": 81.5},
    "WIR1 Comdty": {"NAME": "SHFE Wire Rod Fut", "EXCH_CODE": "SHF", "CRNCY": "CNY", "FUT_VAL_PT": 10.0},
    "CUX6 Comdty": {"PX_LAST": 78450.0, "FUT_LAST_TRADE_DT": date(2026, 11, 16), "FUT_NOTICE_FIRST": None},
    "USDCNY Curncy": {"PX_LAST": 7.12},
}
SECURITY_ERRORS = {"KEE1 Comdty": "Unknown/Invalid security [nid:231]"}
FIELD_ERRORS = {"WIR1 Comdty": {"PX_LAST": "Field not applicable to security",
                                "PX_SETTLE": "Field not applicable to security"}}


class FakeClient:
    """Stands in for BlpapiClient: canned answers, every call recorded."""

    def __init__(self, canned=None, security_errors=None, field_errors=None, search_hits=None):
        self.canned = dict(CANNED if canned is None else canned)
        self.security_errors = dict(SECURITY_ERRORS if security_errors is None else security_errors)
        self.field_errors = dict(FIELD_ERRORS if field_errors is None else field_errors)
        self.search_hits = dict(search_hits or {})
        self.reference_calls, self.search_calls, self.closed = [], [], False

    def reference(self, securities, fields):
        self.reference_calls.append((list(securities), list(fields)))
        out = {}
        for s in securities:
            if s in self.security_errors:
                out[s] = tc.Answer(s, {}, security_error=self.security_errors[s])
            elif s in self.canned or s in self.field_errors:
                values = {k: v for k, v in self.canned.get(s, {}).items() if k in fields and v is not None}
                out[s] = tc.Answer(s, values, field_errors=self.field_errors.get(s, {}))
        return out

    def search(self, query, max_results=50):
        self.search_calls.append(query)
        return list(self.search_hits.get(query, []))

    def close(self):
        self.closed = True


def _factory(client):
    calls = []

    def make(host, port):
        calls.append((host, port))
        return client
    make.calls = calls
    return make


def _check(roots, client=None, **plan_kw):
    the_plan = tc.plan(roots, **plan_kw)
    return tc.run_check(client or FakeClient(), the_plan)


def _one(result, root_id):
    return next(r for r in result.roots if r.root.root_id == root_id)


# --------------------------------------------------------------------------- plan

def test_plan_counts_securities_requests_and_searches_and_asks_nothing():
    roots = [CL, CORN_BAD, NBP, PLACEHOLDER]
    p = tc.plan(roots, batch_size=2, search=True)
    assert p.root_tickers == ["CL1 Comdty", "C 1 Comdty", "FN1 Comdty"]   # one-character root padded
    assert [r.root_id for r in p.placeholders] == ["SHFE:SS"]
    assert p.securities == 3 and p.requests == 2                            # ceil(3 / 2)
    assert p.max_searches == sum(len(tc.search_queries(r)) for r in roots)
    assert tc.plan(roots, root_ids=["nymex:cl"]).root_tickers == ["CL1 Comdty"]
    assert [r.root_id for r in tc.plan(roots, sector="AGRICULTURE").roots] == ["CBOT:ZC"]
    assert len(tc.plan(roots, limit=2).roots) == 2
    with pytest.raises(ValueError, match="close matches: NYMEX:CL"):
        tc.plan(roots, root_ids=["NYMEX:CLL"])


def test_plan_counts_the_book_part():
    fut = tc.BookFuture("CUX26 Comdty", "SHFE:CU", "CNY", 5.0, "CUX6 Comdty", "", "2026-11-30",
                        [("T1", "2026-09-01", 2.0, 78000.0)], None)
    unaskable = dataclasses.replace(fut, instrument_id="ZZSSX26 Comdty", root_id="SHFE:SS", ticker="",
                                    why_no_ticker="placeholder", trades=[("T2", "2026-09-01", 1.0, 13000.0)])
    book = tc.Book(tc.Path("x.db"), AS_OF, [fut, unaskable], [tc.SpotNeed("USDCNY", "USDCNY Curncy", {"T1"})])
    p = tc.plan([CU, CL, PLACEHOLDER], book=book, book_only=True, batch_size=50)
    assert [r.root_id for r in p.roots] == ["SHFE:CU", "SHFE:SS"]           # --book: the roots the book holds
    assert p.contract_tickers == ["CUX6 Comdty"] and p.spot_tickers == ["USDCNY Curncy"]
    assert p.securities == 3 and p.requests == 3                            # roots, contracts, spots
    narrowed = tc.plan([CU, CL, PLACEHOLDER], book=book, root_ids=["SHFE:SS"])
    assert narrowed.contract_tickers == [] and narrowed.spots == []         # a root filter narrows the book


# --------------------------------------------------------------------------- root verdicts

def test_ok_root_and_its_prefilled_worksheet_row():
    client = FakeClient()
    result = _check([CL], client)
    r = _one(result, "NYMEX:CL")
    assert r.verdict == tc.OK and r.findings == []
    assert client.reference_calls == [(["CL1 Comdty"], list(tc.ROOT_FIELDS))]
    rows = tc.worksheet_rows(result)
    assert rows == [{"root_id": "NYMEX:CL", "field": "bbg_verified", "current": "false", "suggested": "true",
                     "verdict": "OK", "reason": rows[0]["reason"], "apply": "yes"}]
    assert "CL1 Comdty resolves" in rows[0]["reason"]


def test_corn_in_cents_against_a_dollar_scale_is_a_scale_mismatch_with_the_suggested_scale():
    r = _one(_check([CORN_BAD]), "CBOT:ZC")
    assert r.verdict == tc.SCALE_MISMATCH
    f = next(f for f in r.findings if f.field == "price_scale")
    assert (f.current, f.suggested) == ("1", "0.01")
    assert "50 USD per contract (FUT_VAL_PT)" in f.evidence and "'USd'" in f.evidence
    assert "multiplier becomes 50" in f.action


def test_nbp_in_pence_agrees_with_a_pence_scale():
    r = _one(_check([NBP]), "ICE:M")
    assert r.verdict == tc.OK, [f.evidence for f in r.findings]


def test_nbp_in_pence_against_a_pound_scale_without_value_per_point():
    canned = dict(CANNED)
    canned["FN1 Comdty"] = {k: v for k, v in CANNED["FN1 Comdty"].items() if k != "FUT_VAL_PT"}
    r = _one(_check([NBP_BAD], FakeClient(canned=canned)), "ICE:M")
    assert r.verdict == tc.SCALE_MISMATCH
    f = r.findings[0]
    assert (f.field, f.current, f.suggested) == ("price_scale", "1", "0.01")
    assert "'GBp'" in f.evidence and "no value per point" in f.evidence


def test_not_found_carries_bloombergs_own_words():
    r = _one(_check([BAD]), "DCE:J")
    assert r.verdict == tc.NOT_FOUND
    assert "Unknown/Invalid security [nid:231]" in r.findings[0].evidence


def test_placeholder_is_not_found_without_being_asked():
    client = FakeClient()
    r = _one(_check([PLACEHOLDER], client), "SHFE:SS")
    assert r.verdict == tc.NOT_FOUND and not r.asked and client.reference_calls == []
    assert "placeholder" in r.findings[0].evidence


def test_no_price_names_the_fields_bloomberg_refused():
    r = _one(_check([DEAD]), "SHFE:WR")
    assert r.verdict == tc.NO_PRICE
    assert "PX_LAST: Field not applicable to security" in r.findings[0].evidence


def test_entitlement_refusal_is_no_price_not_not_found():
    client = FakeClient(security_errors={"CL1 Comdty": "Security not authorized for this user"})
    r = _one(_check([CL], client), "NYMEX:CL")
    assert r.verdict == tc.NO_PRICE and "not authorized" in r.findings[0].evidence


def test_currency_mismatch_but_cnh_counts_as_cny():
    canned = dict(CANNED)
    canned["CU1 Comdty"] = {"NAME": "SHFE Copper Fut", "EXCH_CODE": "SHF", "CRNCY": "CNH", "FUT_VAL_PT": 5.0,
                            "PX_LAST": 78450.0, "FUT_CUR_GEN_TICKER": "CUX6"}
    assert _one(_check([CU], FakeClient(canned=canned)), "SHFE:CU").verdict == tc.OK
    canned["CU1 Comdty"] = dict(canned["CU1 Comdty"], CRNCY="USD")
    r = _one(_check([CU], FakeClient(canned=canned)), "SHFE:CU")
    assert r.verdict == tc.CURRENCY_MISMATCH
    assert (r.findings[0].field, r.findings[0].current, r.findings[0].suggested) == ("currency", "CNY", "USD")


def test_value_per_point_off_by_another_factor_asks_for_a_review_not_a_scale():
    canned = dict(CANNED)
    canned["CL1 Comdty"] = dict(CANNED["CL1 Comdty"], FUT_VAL_PT=42000.0)
    r = _one(_check([CL], FakeClient(canned=canned)), "NYMEX:CL")
    assert r.verdict == tc.SCALE_MISMATCH and r.findings[0].field == ""
    assert "review contract_size" in r.findings[0].action


def test_exchange_and_name_warnings():
    canned = dict(CANNED)
    canned["CL1 Comdty"] = dict(CANNED["CL1 Comdty"], EXCH_CODE="ICE", NAME="BRENT CRUDE FUTR",
                                FUT_CUR_GEN_TICKER="COZ6")
    r = _one(_check([CL], FakeClient(canned=canned)), "NYMEX:CL")
    assert {f.verdict for f in r.findings} == {tc.EXCHANGE_MISMATCH, tc.NAME_CHECK}
    assert r.verdict == tc.EXCHANGE_MISMATCH
    row = tc.worksheet_rows(tc.CheckResult(tc.plan([CL]), [r], [], []))[0]
    assert row["field"] == "bbg_verified" and row["apply"] == ""               # a warning is the user's call


def test_search_lists_candidates_for_a_root_bloomberg_does_not_know():
    hits = {"DCE coke": [("KEE1<cmdty>", "Generic 1st 'KEE' Future"),
                         ("DCJ1<cmdty>", "Generic 1st 'DCJ' Future"),
                         ("JE1<cmdty>", "DCE Coke Future Generic"),
                         ("JEK7<cmdty>", "DCE Metallurgical Coke May27")]}
    client = FakeClient(search_hits=hits)
    result = _check([BAD], client, search=True)
    r = _one(result, "DCE:J")
    assert client.search_calls == ["DCE coke", "coke"]      # exchange and name, then the name alone
    assert r.candidates[0]["root"] == "JE" and r.search_status == "one candidate stands out"
    rows = [w for w in tc.worksheet_rows(result) if w["field"] == "bbg_root"]
    assert [(w["current"], w["suggested"], w["apply"]) for w in rows] == [("KEE", "JE", "")]


# --------------------------------------------------------------------------- the book

def _book_db(tmp_path):
    from data.contracts import store_static_dates
    from data.ingest.schema import connect, create_schema
    path = tmp_path / "risk.db"
    conn = connect(path)
    create_schema(conn)
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('CUX26 Comdty', 'FUTURE', 'SHFE:CU', 'CNY', 5, 0, "
                 "'CUX6 Comdty', '2026-11-30')")
    for trade_id, qty, price in (("T1", 2.0, 784.5), ("T2", -1.0, 786.0)):   # fills in hundreds: 100x off
        conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                     "price, account, counterparty, strategy, trader, description) VALUES (?, 'XLSX', "
                     "'CUX26 Comdty', 'FUTURE', ?, '2026-09-01', ?, ?, 'A', 'C', '', 'J', 'copper')",
                     (trade_id, trade_id, qty, price))
        conn.execute("INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
                     "settles_cash) VALUES (?, 1, 'NOTIONAL', 'CNY', ?, '2026-09-01', '2026-11-30', ?, 0)",
                     (trade_id, qty * 5 * price, price))
    conn.commit()
    store_static_dates(conn, [{"contract_id": "CUX26 Comdty", "last_trade_date": "2026-11-13",
                               "first_notice_date": "", "source": "BBG_BDP"}])
    conn.close()
    return path


def test_book_check_flags_the_fill_scale_and_shows_stored_against_bloomberg_dates(tmp_path):
    db = _book_db(tmp_path)
    book = tc.load_book(db, AS_OF, {CU.root_id: CU})
    assert [f.instrument_id for f in book.futures] == ["CUX26 Comdty"]
    assert book.futures[0].stored["last_trade_date"] == "2026-11-13"
    assert [s.ticker for s in book.spots] == ["USDCNY Curncy"]              # the library's conversion spot
    result = tc.run_check(FakeClient(), tc.plan([CU], book=book, book_only=True))
    c = result.contracts[0]
    assert {f.verdict for f in c.findings} == {tc.SCALE_FLAG, tc.DATE_MISMATCH}
    assert c.verdict == tc.SCALE_FLAG and c.px_last == 78450.0
    assert "about a hundredth of" in next(f.evidence for f in c.findings if f.verdict == tc.SCALE_FLAG)
    date_finding = next(f for f in c.findings if f.verdict == tc.DATE_MISMATCH)
    assert "last trade 2026-11-13 stored, 2026-11-16 on Bloomberg" in date_finding.evidence
    assert result.spots[0].verdict == tc.OK and result.spots[0].px_last == 7.12
    text = tc.render_text(result, now=NOW, paths={})
    assert "2026-11-13 / 2026-11-16" in text                                # stored beside Bloomberg's


def test_book_check_writes_nothing_to_the_database(tmp_path):
    db = _book_db(tmp_path)
    before = db.read_bytes()
    code = tc.main(["--db", str(db), "--root", "SHFE:CU", "--out", str(tmp_path / "reports")],
                   client_factory=_factory(FakeClient()), now=NOW, as_of=AS_OF, roots=[CU])
    assert code == 1                                          # the fills are flagged
    assert db.read_bytes() == before


# --------------------------------------------------------------------------- command line and files

def test_worksheet_columns_and_prefilled_apply(tmp_path):
    out = tmp_path / "reports"
    code = tc.main(["--out", str(out)], client_factory=_factory(FakeClient()), now=NOW, roots=[CL, CORN_BAD])
    assert code == 1
    stamp = "20260924_100000"
    with open(out / f"contract_fixes_{stamp}.csv", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames) == tc.WORKSHEET_COLUMNS
        rows = list(reader)
    by_key = {(r["root_id"], r["field"]): r for r in rows}
    assert by_key[("NYMEX:CL", "bbg_verified")]["apply"] == "yes"
    scale = by_key[("CBOT:ZC", "price_scale")]
    assert (scale["current"], scale["suggested"], scale["verdict"]) == ("1", "0.01", "SCALE_MISMATCH")
    assert scale["apply"] == ""
    assert [r["apply"] for r in rows if r["apply"]] == ["yes"]
    text = (out / f"bbg_check_{stamp}.txt").read_text(encoding="utf-8")
    assert "SCALE_MISMATCH" in text and "CBOT:ZC" in text and "OK: NYMEX:CL" in text
    with open(out / f"bbg_check_{stamp}.csv", encoding="utf-8", newline="") as fh:
        fields = list(csv.DictReader(fh))
    corn = next(f for f in fields if f["security"] == "C 1 Comdty")
    assert corn["CRNCY"] == "USd" and corn["FUT_VAL_PT"] == "50" and corn["verdict"] == "SCALE_MISMATCH"


def test_every_root_ok_exits_0(tmp_path, capsys):
    client = FakeClient()
    code = tc.main(["--out", str(tmp_path)], client_factory=_factory(client), now=NOW, roots=[CL, NBP])
    assert code == 0 and client.closed
    out = capsys.readouterr().out
    assert "Asking Bloomberg on localhost:8194 for 2 securities in 1 requests" in out
    assert "Report:" in out and "Worksheet:" in out


def test_dry_run_asks_nothing_and_writes_nothing(tmp_path, capsys):
    factory = _factory(FakeClient())
    code = tc.main(["--dry-run", "--out", str(tmp_path / "reports")], client_factory=factory, now=NOW,
                   roots=[CL, CORN_BAD, PLACEHOLDER])
    assert code == 0 and factory.calls == []
    assert not (tmp_path / "reports").exists()
    out = capsys.readouterr().out
    assert "2 securities in 1 ReferenceDataRequest" in out and "nothing was asked" in out


def test_no_blpapi_exits_2_with_the_reason(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "blpapi", None)          # `import blpapi` raises ImportError
    code = tc.main(["--root", "NYMEX:CL", "--out", str(tmp_path / "reports")], now=NOW, roots=[CL])
    assert code == 2
    assert "blpapi is not installed" in capsys.readouterr().out
    assert not (tmp_path / "reports").exists()


def test_no_session_exits_2_with_the_reason(tmp_path, capsys):
    def refuse(host, port):
        raise tc.BloombergUnavailable(f"no Bloomberg API service on {host}:{port} (refused)")
    code = tc.main(["--root", "NYMEX:CL", "--host", "bbg-pc", "--port", "8195", "--out", str(tmp_path)],
                   client_factory=refuse, now=NOW, roots=[CL])
    assert code == 2 and "no Bloomberg API service on bbg-pc:8195" in capsys.readouterr().out


# --------------------------------------------------------------------------- the real client, fake blpapi

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


class _Msg(_El):
    def __init__(self, data, cid):
        super().__init__(data)
        self._cid = cid

    def correlationIds(self):
        return [self._cid]


class _Event:
    def __init__(self, msgs, kind):
        self._m, self._k = msgs, kind

    def __iter__(self):
        return iter(self._m)

    def eventType(self):
        return self._k


class _List:
    def __init__(self):
        self.items = []

    def appendValue(self, v):
        self.items.append(v)


class _Req:
    def __init__(self, kind):
        self.kind, self.lists, self.values = kind, {}, {}

    def getElement(self, name):
        return self.lists.setdefault(name, _List())

    def set(self, name, value):
        self.values[name] = value


class _Session:
    def __init__(self, opts=None):
        self.sent, self.services = [], []

    def start(self):
        return True

    def openService(self, name):
        self.services.append(name)
        return True

    def getService(self, name):
        return self

    def createRequest(self, kind):
        return _Req(kind)

    def sendRequest(self, request, correlationId=None):
        self.sent.append((request, correlationId))

    def nextEvent(self, timeout=None):
        request, cid = self.sent[-1]
        if request.kind == "instrumentListRequest":
            data = {"results": [{"security": "JE1<cmdty>", "description": "DCE Coke Future Generic"}]}
        else:
            data = {"securityData": [
                {"security": "CL1 Comdty", "fieldData": {"NAME": "Generic 1st 'CL' Future", "PX_LAST": 72.15},
                 "fieldExceptions": [{"fieldId": "FUT_VAL_PT", "errorInfo": {"message": "Field not valid"}}]},
                {"security": "KEE1 Comdty", "securityError": {"message": "Unknown/Invalid security [nid:231]"}},
            ]}
        return _Event([_Msg(data, cid)], "RESPONSE")

    def stop(self):
        pass


def _fake_blpapi():
    class _Cid:
        def __init__(self, v):
            self.v = v

        def __eq__(self, other):
            return isinstance(other, _Cid) and other.v == self.v

        def __hash__(self):
            return hash(self.v)

    class _Opts:
        def setServerHost(self, h):
            pass

        def setServerPort(self, p):
            pass

    mod = types.ModuleType("blpapi")
    mod.SessionOptions = _Opts
    mod.Session = _Session
    mod.CorrelationId = _Cid
    mod.Event = types.SimpleNamespace(RESPONSE="RESPONSE", TIMEOUT="TIMEOUT", PARTIAL_RESPONSE="PARTIAL_RESPONSE")
    return mod


def test_blpapi_client_turns_bloombergs_answer_into_answers(monkeypatch):
    monkeypatch.setitem(sys.modules, "blpapi", _fake_blpapi())
    from data.bloomberg import live
    monkeypatch.setattr(live, "availability", lambda host, port, timeout=0.5: (True, ""))
    client = tc.BlpapiClient("localhost", 8194)
    got = client.reference(["CL1 Comdty", "KEE1 Comdty"], ["NAME", "PX_LAST", "FUT_VAL_PT"])
    assert got["CL1 Comdty"].fields == {"NAME": "Generic 1st 'CL' Future", "PX_LAST": 72.15}
    assert got["CL1 Comdty"].field_errors == {"FUT_VAL_PT": "Field not valid"}
    assert got["KEE1 Comdty"].security_error == "Unknown/Invalid security [nid:231]"
    assert client.search("DCE coke") == [("JE1<cmdty>", "DCE Coke Future Generic")]
    assert tc.INSTRUMENTS_SERVICE in client.session.services
    request = client.session.sent[-1][0]
    assert request.values["yellowKeyFilter"] == tc.YELLOW_KEY_FILTER and request.values["query"] == "DCE coke"
    client.close()


def test_a_root_starting_with_a_digit_is_its_own_front_contract():
    root = _root(root_id="NYMEX:7H", name="NYMEX Eurobob oxy gasoline NWE barges (Argus)", exchange="NYMEX",
                 exchange_code="7H", bbg_root="7H", currency="USD", contract_size=1000.0, size_unit="t",
                 quote_unit="USD/t", price_scale=1.0, multiplier=1000.0, calendar="US")
    canned = {"7H1 Comdty": {"NAME": "Generic 1st '7H' Future", "EXCH_CODE": "NYM", "CRNCY": "USD",
                             "FUT_VAL_PT": 1000.0, "FUT_CUR_GEN_TICKER": "7HZ6", "PX_LAST": 700.0}}
    assert _one(_check([root], FakeClient(canned=canned)), "NYMEX:7H").verdict == tc.OK
    assert tc.parse_search_security("7H1<cmdty>", "Generic 1st '7H' Future") == ("7H", "generic")


# --------------------------------------------------------------------------- Phase 5: LME curve tickers

CA = load_roots()["LME:CA"]                                       # a real LME forward metal (engine.lme checks it)
CA_3M = _lme.three_month_date(AS_OF).isoformat()                 # 2026-12-24
CA_MONTHLY = _lme.monthly_prompt(2026, 10).isoformat()           # 2026-10-21, the first third Wednesday after cash
LME_CANNED = {
    "LMCADY Comdty": {"NAME": "LME COPPER SPOT ($)", "CRNCY": "USD", "PX_LAST": 9800.0,
                      "FUT_DLV_DT_LAST": _lme.cash_date(AS_OF)},
    "LMCADS03 Comdty": {"NAME": "LME COPPER 3MO ($)", "CRNCY": "USD", "PX_LAST": 9850.0, "FUT_DLV_DT_LAST": CA_3M},
    "LPV6 Comdty": {"NAME": "LME COPPER FUTURE Oct26", "CRNCY": "USD", "PX_LAST": 9820.0,
                    "FUT_DLV_DT_LAST": CA_MONTHLY},
}


def _lme_check(canned=None, security_errors=None):
    client = FakeClient(canned=dict(LME_CANNED if canned is None else canned), security_errors=security_errors or {},
                        field_errors={})
    return tc.run_check(client, tc.plan([CA], parts=["lme"], today=AS_OF)), client


def test_lme_curve_tickers_ok_in_one_request():
    result, client = _lme_check()
    assert client.reference_calls == [(["LMCADY Comdty", "LMCADS03 Comdty", "LPV6 Comdty"], list(tc.lme_fields()))]
    assert "FUT_DLV_DT_LAST" in tc.lme_fields()
    metal = result.lme[0]
    assert metal.verdict == tc.OK, [(p.label, p.findings) for p in metal.pillars]
    assert [p.bbg_date for p in metal.pillars] == ["2026-09-28", CA_3M, CA_MONTHLY]
    assert result.roots == [] and tc.worksheet_rows(result) == []      # --lme alone: no root check, no rows
    assert not result.needs_attention


def test_lme_ticker_bloomberg_does_not_know_is_not_found_for_the_housekeeper():
    result, _client = _lme_check(security_errors={"LMCADS03 Comdty": "Unknown/Invalid security [nid:231]"})
    metal = result.lme[0]
    three = next(p for p in metal.pillars if p.kind == "3M")
    assert metal.verdict == three.verdict == tc.NOT_FOUND
    assert "Unknown/Invalid security [nid:231]" in three.findings[0].evidence          # Bloomberg's own words
    assert three.findings[0].owner == "lme-forwards" and "three_month_ticker" in three.findings[0].action
    assert tc.worksheet_rows(result) == []                                              # code, not the CSV
    text = tc.render_text(result, now=NOW, paths={})
    assert "For the housekeeper" in text and "lme-forwards: LME:CA 3M [LMCADS03 Comdty]" in text


def test_lme_monthly_prompt_off_our_third_wednesday_is_a_date_mismatch():
    canned = dict(LME_CANNED)
    canned["LPV6 Comdty"] = dict(LME_CANNED["LPV6 Comdty"], FUT_DLV_DT_LAST="2026-10-22")
    result, _client = _lme_check(canned=canned)
    monthly = next(p for p in result.lme[0].pillars if p.kind == "MONTHLY")
    assert monthly.verdict == tc.DATE_MISMATCH
    assert "2026-10-22" in monthly.findings[0].evidence and CA_MONTHLY in monthly.findings[0].evidence
    assert "monthly_prompt" in monthly.findings[0].action


def test_lme_3m_not_rolled_yet_is_a_note_and_a_non_usd_answer_is_a_currency_mismatch():
    canned = dict(LME_CANNED)
    canned["LMCADS03 Comdty"] = dict(LME_CANNED["LMCADS03 Comdty"], FUT_DLV_DT_LAST="2026-12-23")   # yesterday's 3M
    canned["LMCADY Comdty"] = dict(LME_CANNED["LMCADY Comdty"], CRNCY="GBP")
    result, _client = _lme_check(canned=canned)
    pillars = {p.kind: p for p in result.lme[0].pillars}
    assert pillars["3M"].verdict == tc.OK and "not rolled" in pillars["3M"].notes[0]
    assert pillars["CASH"].verdict == tc.CURRENCY_MISMATCH


# --------------------------------------------------------------------------- Phase 5: options on futures

CL_OPT = dataclasses.replace(CL, option_style="american", option_lead_months=1)
OPTION_CANNED = {
    "CLX6C 70 Comdty": {"NAME": "CLX6C 70", "CRNCY": "USD", "PX_LAST": 3.2, "OPT_EXPIRE_DT": date(2026, 10, 15),
                        "OPT_EXER_TYP": "American", "OPT_STRIKE_PX": 70.0, "OPT_UNDL_TICKER": "CLX6 Comdty",
                        "OPT_UNDL_PX": 72.15},
}
CHAIN_OURS = ["CLX6C 60 Comdty", "CLX6C 70 Comdty", "CLX6C 80 Comdty", "CLX6P 70 Comdty", "CLZ6C 70 Comdty"]


class ChainClient(FakeClient):
    """FakeClient with the bulk OPT_CHAIN request: ``chains`` maps a generic to its option tickers."""

    def __init__(self, chains, **kw):
        super().__init__(**kw)
        self.chains = chains
        self.bulk_calls = []

    def bulk(self, securities, field_name):
        self.bulk_calls.append((list(securities), field_name))
        out = {}
        for s in securities:
            if s in self.security_errors:
                out[s] = tc.Answer(s, {}, security_error=self.security_errors[s])
            elif s in self.chains:
                rows = [{"Security Description": t} for t in self.chains[s]]
                out[s] = tc.Answer(s, {field_name: rows} if rows else {},
                                   field_errors={} if rows else {field_name: "Field not applicable to security"})
        return out


def _options_check(chain, canned, root=CL_OPT, security_errors=None):
    client = ChainClient({"CL1 Comdty": chain}, canned=canned, security_errors=security_errors or {},
                         field_errors={})
    result = tc.run_check(client, tc.plan([root], parts=["options"], today=AS_OF))
    return result.options[0], result, client


def test_option_chain_in_our_form_is_ok():
    o, result, client = _options_check(CHAIN_OURS, OPTION_CANNED)
    assert client.bulk_calls == [(["CL1 Comdty"], "OPT_CHAIN")]
    assert o.picked == "CLX6C 70 Comdty" and o.ours == ""        # nearest month, calls, middle strike; same form
    assert client.reference_calls == [(["CLX6C 70 Comdty"], list(tc.OPTION_FIELDS))]
    assert o.verdict == tc.OK, [f.evidence for f in o.findings]
    assert (o.bbg_style, o.bbg_lead, o.canonical) == ("AMERICAN", 1, "CLX26C 70 Comdty")
    assert o.our_expiry and o.bbg_expiry == "2026-10-15"
    assert tc.worksheet_rows(result) == []


def test_option_chain_in_another_form_that_bloomberg_refuses_in_ours_is_a_form_mismatch():
    chain = ["CLX6C 60.00 Comdty", "CLX6C 70.00 Comdty", "CLX6C 80.00 Comdty"]
    canned = {"CLX6C 70.00 Comdty": OPTION_CANNED["CLX6C 70 Comdty"]}
    o, result, client = _options_check(chain, canned,
                                       security_errors={"CLX6C 70 Comdty": "Unknown/Invalid security [nid:231]"})
    assert (o.picked, o.ours) == ("CLX6C 70.00 Comdty", "CLX6C 70 Comdty")
    assert client.reference_calls == [(["CLX6C 70.00 Comdty", "CLX6C 70 Comdty"], list(tc.OPTION_FIELDS))]
    assert o.verdict == tc.FORM_MISMATCH
    f = next(f for f in o.findings if f.verdict == tc.FORM_MISMATCH)
    assert "Unknown/Invalid security [nid:231]" in f.evidence and f.owner == "contract-master"
    assert tc.worksheet_rows(result) == []                        # a form is code: a housekeeper line instead
    assert any(owner == "contract-master" for owner, _s, _f in result.code_findings())


def test_option_form_that_differs_but_resolves_to_the_same_option_is_a_note():
    canned = {"CLX6C 70.00 Comdty": OPTION_CANNED["CLX6C 70 Comdty"],
              "CLX6C 70 Comdty": OPTION_CANNED["CLX6C 70 Comdty"]}
    o, _result, _client = _options_check(["CLX6C 70.00 Comdty"], canned)
    assert o.verdict == tc.OK and "resolves to the same option" in " ".join(o.notes)


def test_option_style_and_lead_mismatches_are_worksheet_rows():
    canned = {"CLX6C 70 Comdty": dict(OPTION_CANNED["CLX6C 70 Comdty"], OPT_EXER_TYP="European",
                                      OPT_EXPIRE_DT=date(2026, 9, 17))}      # two months before the Nov future
    o, result, _client = _options_check(CHAIN_OURS, canned)
    assert {f.verdict for f in o.findings} == {tc.STYLE_MISMATCH, tc.LEAD_MISMATCH}
    rows = {(w["field"], w["current"], w["suggested"], w["apply"]) for w in tc.worksheet_rows(result)}
    assert rows == {("option_style", "american", "european", ""), ("option_lead_months", "1", "2", "")}


def test_no_option_chain_is_no_options_with_bloombergs_words():
    o, _result, client = _options_check([], OPTION_CANNED)
    assert o.verdict == tc.NO_OPTIONS and "Field not applicable to security" in o.findings[0].evidence
    assert client.reference_calls == []                            # no option to ask


# --------------------------------------------------------------------------- Phase 5: the book's options and LME

def _phase5_db(tmp_path):
    from data.ingest.schema import connect, create_schema
    path = tmp_path / "risk.db"
    conn = connect(path)
    create_schema(conn)
    instrument = ("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                  "bbg_ticker, expiry_date) VALUES (?, ?, ?, 'USD', ?, 0, ?, ?)")
    conn.execute(instrument, ("CLX26C 70 Comdty", "CMDTY_OPTION", "NYMEX:CL", 1000, "CLX6C 70 Comdty", "2026-11-30"))
    conn.execute(instrument, ("LME:CA", "LME_FWD", "LME:CA", 1, "LMCADY Comdty", "9999-12-31"))
    trade = ("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
             "account, counterparty, strategy, trader, description) VALUES (?, 'XLSX', ?, ?, ?, ?, ?, ?, 'A', 'C', '', "
             "'J', '')")
    leg = ("INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
           "settles_cash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
    conn.execute(trade, ("O1", "CLX26C 70 Comdty", "CMDTY_OPTION", "O1", "2026-09-01", 2.0, 320.0))   # 100x off
    conn.execute(leg, ("O1", 1, "NOTIONAL", "USD", 640000.0, "2026-09-01", "2026-11-30", 320.0, 0))
    for tid, prompt in (("L1", CA_MONTHLY), ("L2", "2026-11-04"), ("L3", "2031-12-17"), ("L4", "2026-10-24")):
        conn.execute(trade, (tid, "LME:CA", "LME_FWD", tid, "2026-09-22", 50.0, 9750.0))
        conn.execute(leg, (tid, 1, "FX_NEAR", "LME:CA", 50.0, "2026-09-22", prompt, 9750.0, 0))
        conn.execute(leg, (tid, 2, "FX_NEAR", "USD", -487500.0, "2026-09-22", prompt, 9750.0, 1))
    conn.commit()
    conn.close()
    return path


def test_book_mode_checks_the_books_options_and_places_its_lme_tickets(tmp_path):
    db = _phase5_db(tmp_path)
    before = db.read_bytes()
    book = tc.load_book(db, AS_OF, {CL_OPT.root_id: CL_OPT, CA.root_id: CA})
    assert [o.instrument_id for o in book.options] == ["CLX26C 70 Comdty"] and book.options[0].kind == "CMDTY_OPTION"
    assert [t.trade_id for t in book.lme] == ["L1", "L4", "L2", "L3"]            # by prompt
    client = FakeClient(canned={**LME_CANNED, **OPTION_CANNED}, security_errors={}, field_errors={})
    the_plan = tc.plan([CL_OPT, CA], book=book, book_only=True, parts=["lme"], today=AS_OF)
    assert the_plan.book_option_tickers == ["CLX6C 70 Comdty"] and [m.root.root_id for m in the_plan.lme] == ["LME:CA"]
    result = tc.run_check(client, the_plan)
    assert (["CLX6C 70 Comdty"], list(tc.BOOK_OPTION_FIELDS)) in client.reference_calls
    opt = result.book_options[0]
    assert {f.verdict for f in opt.findings} == {tc.SCALE_FLAG, tc.DATE_MISMATCH}
    assert "about 100 times" in next(f.evidence for f in opt.findings if f.verdict == tc.SCALE_FLAG)
    assert "2026-11-30" in next(f.evidence for f in opt.findings if f.verdict == tc.DATE_MISMATCH)
    tickets = {t.ticket.trade_id: t for t in result.book_lme}
    assert tickets["L1"].verdict == tc.OK and "on the 2026-10 pillar" in tickets["L1"].position
    assert tickets["L2"].verdict == tc.OK and "interpolated" in tickets["L2"].position
    assert tickets["L3"].verdict == tc.OFF_CURVE
    assert tickets["L4"].verdict == tc.NOT_A_PROMPT                  # a Saturday
    text = tc.render_text(result, now=NOW, paths={})
    assert "Options on futures: 1 open, 1 flagged" in text and "LME tickets: 4 open, 2 flagged" in text
    assert db.read_bytes() == before                                 # read-only


def test_book_lme_ticket_without_a_priced_curve_is_no_curve(tmp_path):
    db = _phase5_db(tmp_path)
    book = tc.load_book(db, AS_OF, {CL_OPT.root_id: CL_OPT, CA.root_id: CA})
    refused = {"LMCADY Comdty": "Security not authorized", "LMCADS03 Comdty": "Security not authorized"}
    client = ChainClient({"CL1 Comdty": CHAIN_OURS}, canned=OPTION_CANNED, security_errors=refused, field_errors={})
    result = tc.run_check(client, tc.plan([CL_OPT, CA], book=book, book_only=True, parts=["options"], today=AS_OF))
    assert [m.root.root_id for m in result.lme] == ["LME:CA"]        # asked for the book's tickets whatever the parts
    assert tc.NO_CURVE in {t.verdict for t in result.book_lme}


# --------------------------------------------------------------------------- Phase 5: counts and the command line

def test_dry_run_counts_the_lme_and_option_asks(tmp_path, capsys):
    factory = _factory(FakeClient())
    code = tc.main(["--dry-run", "--lme", "--options", "--out", str(tmp_path)], client_factory=factory, now=NOW,
                   as_of=AS_OF, roots=[CL_OPT, CA, CORN_BAD])
    out = capsys.readouterr().out
    assert code == 0 and factory.calls == []
    assert "contract root" not in out                                 # the root check is not run
    assert "LME curve on 2026-09-24: 1 metal, 3 tickers (cash, 3M and the monthly 2026-10 of each)" in out
    assert "1 option chain (OPT_CHAIN on the generic front future) in 1 request of up to 10" in out
    assert "4 securities in 2 ReferenceDataRequests (up to 50 each), then up to 2 option tickers in up to 1 more" in out
    code = tc.main(["--dry-run", "--out", str(tmp_path)], client_factory=factory, now=NOW, as_of=AS_OF,
                   roots=[CL_OPT, CA, CORN_BAD])
    out = capsys.readouterr().out
    assert "3 contract roots selected" in out                          # a plain run: every part
    assert "7 securities in 3 ReferenceDataRequests" in out            # 3 generics + 3 LME + 1 chain


def test_plan_refuses_an_unknown_part():
    with pytest.raises(ValueError, match="unknown part"):
        tc.plan([CL], parts=["rates"])


def test_full_run_writes_option_rows_and_housekeeper_lines(tmp_path, capsys):
    canned = {**CANNED, **LME_CANNED,
              "CLX6C 70 Comdty": dict(OPTION_CANNED["CLX6C 70 Comdty"], OPT_EXER_TYP="European")}
    client = ChainClient({"CL1 Comdty": CHAIN_OURS}, canned=canned,
                         security_errors={"LMCADS03 Comdty": "Unknown/Invalid security"})
    out_dir = tmp_path / "reports"
    code = tc.main(["--out", str(out_dir)], client_factory=_factory(client), now=NOW, as_of=AS_OF, roots=[CL_OPT, CA])
    assert code == 1
    printed = capsys.readouterr().out
    assert "Option roots: 1 STYLE_MISMATCH." in printed and "LME metals: 1 NOT_FOUND." in printed
    assert "For the housekeeper: 1 finding" in printed
    with open(out_dir / "contract_fixes_20260924_100000.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert ("NYMEX:CL", "option_style", "european") in {(r["root_id"], r["field"], r["suggested"]) for r in rows}
    with open(out_dir / "bbg_check_20260924_100000.csv", encoding="utf-8", newline="") as fh:
        fields = list(csv.DictReader(fh))
    assert next(f for f in fields if f["part"] == "option_chain")["OPT_CHAIN"] == "5 rows"
    assert {f["part"] for f in fields} >= {"root", "lme_cash", "lme_3m", "lme_monthly", "option"}


# --------------------------------------------------------------------------- the real client's bulk request

class _Arr:
    """A blpapi bulk field: an array of sequences."""

    def __init__(self, rows):
        self.rows = rows

    def numValues(self):
        return len(self.rows)

    def getValueAsElement(self, i):
        return _Row(self.rows[i])


class _Sub:
    def __init__(self, name, value):
        self._n, self._v = name, value

    def name(self):
        return self._n

    def getValue(self):
        return self._v


class _Row:
    def __init__(self, d):
        self.items = list(d.items())

    def numElements(self):
        return len(self.items)

    def getElement(self, j):
        return _Sub(*self.items[j])


class _BulkEl(_El):
    def getElement(self, name):
        v = self._v[name]
        return v if isinstance(v, _Arr) else _BulkEl(v)

    def getValueAsElement(self, i):
        return _BulkEl(self._v[i])


class _BulkMsg(_BulkEl):
    def __init__(self, data, cid):
        super().__init__(data)
        self._cid = cid

    def correlationIds(self):
        return [self._cid]


class _BulkSession(_Session):
    def nextEvent(self, timeout=None):
        _request, cid = self.sent[-1]
        chain = _Arr([{"Security Description": "CLX6C 70 Comdty"}, {"Security Description": "CLX6P 70 Comdty"}])
        data = {"securityData": [
            {"security": "CL1 Comdty", "fieldData": {"OPT_CHAIN": chain}},
            {"security": "ZZ1 Comdty", "securityError": {"message": "Unknown/Invalid security"}},
        ]}
        return _Event([_BulkMsg(data, cid)], "RESPONSE")


def test_blpapi_client_reads_a_bulk_field(monkeypatch):
    mod = _fake_blpapi()
    mod.Session = _BulkSession
    monkeypatch.setitem(sys.modules, "blpapi", mod)
    from data.bloomberg import live
    monkeypatch.setattr(live, "availability", lambda host, port, timeout=0.5: (True, ""))
    client = tc.BlpapiClient("localhost", 8194)
    got = client.bulk(["CL1 Comdty", "ZZ1 Comdty"], "OPT_CHAIN")
    assert tc.chain_tickers(got["CL1 Comdty"].fields["OPT_CHAIN"]) == ["CLX6C 70 Comdty", "CLX6P 70 Comdty"]
    assert got["ZZ1 Comdty"].security_error == "Unknown/Invalid security"
    assert client.session.sent[-1][0].lists["fields"].items == ["OPT_CHAIN"]
