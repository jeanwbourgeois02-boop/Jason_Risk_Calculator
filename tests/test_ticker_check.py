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

NOW = datetime(2026, 9, 24, 10, 0, 0)
AS_OF = date(2026, 9, 24)


def _root(**kw):
    """A ContractRoot built from any row of config/contracts.csv, so the tests do not depend on
    which roots the file holds or on its current guesses."""
    base = next(iter(load_roots().values()))
    defaults = dict(sector="energy", subsector="", country="US", bbg_yellow_key="Comdty", bbg_verified=False,
                    active_months=tuple(range(1, 13)), delivery="physical", status="active", notes="")
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
