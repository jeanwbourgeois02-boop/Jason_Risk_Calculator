"""tools/bloomberg_terminal_probe.py runs standalone; without Bloomberg it must report
unavailability cleanly (exit 1, both reports written, no fake prices, DB untouched)."""
import importlib.util
import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_tool():
    spec = importlib.util.spec_from_file_location("bbg_diag", REPO / "tools" / "bloomberg_terminal_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_db(path: Path) -> None:
    from data.ingest import schema
    from datetime import date, timedelta
    # Settle date relative to the real clock: the tool's ledger check counts trades
    # settled before today with no realised row, so a fixed 2026-09-16 turned the
    # "0 realised trade(s)" assertion below into a time bomb once that day passed.
    settle = (date.today() + timedelta(days=60)).isoformat()
    conn = schema.connect(path)
    conn.execute("INSERT INTO instruments VALUES ('AUDUSD','FX','AUD','USD',1,0,'AUDUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('a1','MANUAL','AUDUSD','FX_FWD','a1','2026-08-10',-1e6,0.65,'acc','cp','HAHY7','t','d','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", settle, 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", settle, 0.65, 1)])
    conn.commit()
    conn.close()


def test_no_bloomberg_gives_clean_unavailable_report(tmp_path, monkeypatch, capsys):
    tool = _load_tool()
    db = tmp_path / "risk.db"
    _seed_db(db)
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    # force the no-Bloomberg path even on a machine that has blpapi
    monkeypatch.setattr(tool, "check_blpapi", lambda rep: (rep.check("blpapi", False, "import failed: simulated"), None)[1])
    monkeypatch.setattr(tool, "check_tcp", lambda rep, host, port: (rep.check("tcp", False, "refused: simulated"), False)[1])
    code = tool.main(["--once", "--db", str(db), "--out", str(tmp_path / "reports"), "--port", "1"])
    assert code == 1
    out = capsys.readouterr().out
    assert "BLOOMBERG UNAVAILABLE OR INCOMPLETE" in out and "Traceback" not in out
    reports = sorted((tmp_path / "reports").glob("bloomberg_diagnostic_*"))
    assert [p.suffix for p in reports] == [".json", ".txt"]
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    assert data["all_required_ok"] is False
    assert data["checks"]["python"]["ok"] is True and data["checks"]["database"]["ok"] is True
    assert data["checks"]["database"]["pairs"] == ["AUDUSD"]
    for name in ("blpapi", "tcp", "session", "service", "spot", "forward"):
        assert data["checks"][name]["ok"] is False
    assert data["tickers"] == []                                        # no prices of any kind
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM marks").fetchone()[0] == before  # read-only
    txt = reports[1].read_text(encoding="utf-8")
    assert "RESULT: BLOOMBERG UNAVAILABLE" in txt and "Read-only run" in txt


def test_ledger_and_feed_sections_are_informational_and_read_only(tmp_path, monkeypatch, capsys):
    tool = _load_tool()
    db = tmp_path / "risk.db"
    _seed_db(db)                                             # schema.connect creates the ledger tables too
    import json as _json
    (tmp_path / "risk.db.bloomberg_status.json").write_text(_json.dumps(
        {"time": "t", "connected": True, "written": 3, "failed": 0, "skipped": 1,
         "ledger": {"as_of_date": "2026-09-14", "complete": False, "realised_trades": 0}}), encoding="utf-8")
    monkeypatch.setattr(tool, "check_blpapi", lambda rep: (rep.check("blpapi", False, "simulated"), None)[1])
    monkeypatch.setattr(tool, "check_tcp", lambda rep, host, port: (rep.check("tcp", False, "simulated"), False)[1])
    code = tool.main(["--once", "--db", str(db), "--out", str(tmp_path / "reports"), "--port", "1"])
    assert code == 1                                          # required checks still decide the exit code
    data = json.loads(next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8"))
    led = data["checks"]["ledger"]
    assert led["required"] is False and led["ok"] is True and led["realised_trades"] == 0
    assert "0 realised trade(s)" in led["detail"]
    feed = data["checks"]["feed"]
    assert feed["required"] is False and feed["ok"] is True and "skipped=1" in feed["detail"] and "ledger:" in feed["detail"]
    txt = next((tmp_path / "reports").glob("*.txt")).read_text(encoding="utf-8")
    assert "Informational (not required for exit 0)" in txt and "] ledger" in txt
    assert "] ledger" in capsys.readouterr().out
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 0   # read-only


def test_missing_database_is_reported_not_raised(tmp_path, capsys):
    tool = _load_tool()
    code = tool.main(["--once", "--db", str(tmp_path / "nope.db"), "--out", str(tmp_path / "r"), "--port", "1"])
    assert code == 1
    data = json.loads(next((tmp_path / "r").glob("*.json")).read_text(encoding="utf-8"))
    assert data["checks"]["database"]["ok"] is False and "does not exist" in data["checks"]["database"]["detail"]


# --------------------------------------------------------------------------- 2026-09-21 informational probes
# The forward-points divisor fields (FWD_POINTS_SCALE / FWD_SCALE, one request) and one
# IntradayBarRequest for the 15:00 New York close bar: each prints exactly what came back.
class _El:
    """Just enough of a blpapi Element over plain dicts / lists / scalars."""

    def __init__(self, v):
        self._v = v

    def hasElement(self, name):
        return isinstance(self._v, dict) and name in self._v

    def getElement(self, name):
        return _El(self._v[name])

    def getElementAsString(self, name):
        return str(self._v[name])

    def values(self):
        return [_El(x) for x in self._v]

    def numValues(self):
        return len(self._v) if isinstance(self._v, list) else 1

    def getValueAsElement(self, i):
        return _El(self._v[i])

    def isArray(self):
        return isinstance(self._v, list)

    def getValueAsFloat(self):
        return float(self._v)

    def getValue(self):
        return self._v

    def __str__(self):
        return str(self._v)


class _Msg(_El):
    pass


class _Event:
    RESPONSE, TIMEOUT = "RESPONSE", "TIMEOUT"

    def __init__(self, msgs):
        self._msgs = msgs

    def __iter__(self):
        return iter(self._msgs)

    def eventType(self):
        return _Event.RESPONSE


class _Blpapi:
    Event = _Event


class _List:
    def __init__(self):
        self.items = []

    def appendValue(self, v):
        self.items.append(v)


class _Request:
    def __init__(self, kind):
        self.kind, self.lists, self.scalars = kind, {}, {}

    def getElement(self, name):
        return self.lists.setdefault(name, _List())

    def set(self, name, value):
        self.scalars[name] = value


class _Session:
    """Answers FWD_SCALE only (FWD_POINTS_SCALE is a field exception), and one BID bar."""

    def __init__(self):
        self.sent, self._next = [], None

    def createRequest(self, kind):
        return _Request(kind)

    def sendRequest(self, request):
        self.sent.append(request)
        if request.kind == "ReferenceDataRequest":
            scale = {"EURUSD Curncy": 4, "USDJPY Curncy": 2}
            self._next = {"securityData": [
                {"security": t, "fieldData": {"FWD_SCALE": scale[t]},
                 "fieldExceptions": [{"fieldId": "FWD_POINTS_SCALE", "errorInfo": {"message": "Field not valid"}}]}
                for t in request.getElement("securities").items]}
        else:
            self._next = {"barData": {"barTickData": [{"time": request.scalars["startDateTime"], "close": 1.1712}]}}

    def nextEvent(self, timeout=None):
        return _Event([_Msg(self._next)])


def test_informational_probes_print_both_scale_fields_and_the_1500_close_bar(capsys):
    tool = _load_tool()
    rep, session = tool.Report(), _Session()
    tool.check_fwd_scale(rep, _Blpapi, session, session)
    tool.check_intraday_close(rep, _Blpapi, session, session)

    scale = rep.checks["fwdscale"]
    assert scale["required"] is False and scale["ok"] is True
    assert session.sent[0].getElement("fields").items == ["FWD_POINTS_SCALE", "FWD_SCALE"]     # ONE request, both fields
    assert "divisor 10000 from FWD_SCALE" in scale["detail"] and "divisor 100 from FWD_SCALE" in scale["detail"]
    assert "FWD_POINTS_SCALE: Field not valid" in scale["detail"]                               # Bloomberg's own words

    bar = rep.checks["intraday"]
    request = session.sent[1]
    assert request.kind == "IntradayBarRequest" and request.scalars["security"] == "EURUSD Curncy"
    assert request.scalars["eventType"] == "BID" and request.scalars["interval"] == 60
    assert request.scalars["gapFillInitialBar"] is True
    start, end = request.scalars["startDateTime"], request.scalars["endDateTime"]
    assert (end - start).total_seconds() == 3600 and start.tzinfo is None                      # one hour, UTC, naive
    assert end.hour in (19, 20) and start.weekday() < 5                                         # 15:00 New York, a weekday
    assert bar["required"] is False and bar["ok"] is True and "1.1712" in bar["detail"]
    assert "14:00-15:00 New York" in bar["detail"]
    assert rep.all_required_ok is False                       # informational: they decide nothing about exit 0
    assert "] fwdscale" in capsys.readouterr().out


class _AnswerAllSession(_Session):
    """Every ReferenceDataRequest security gets a number for every field asked, except
    'ADSO1 Curncy' (a securityError); the HistoricalDataRequest answers two days, one
    carrying SETTLE_DT."""

    def sendRequest(self, request):
        self.sent.append(request)
        if request.kind == "ReferenceDataRequest":
            fields = request.getElement("fields").items
            rows = []
            for t in request.getElement("securities").items:
                if t == "ADSO1 Curncy":
                    rows.append({"security": t, "securityError": {"message": "Unknown/Invalid Security"}})
                    continue
                rows.append({"security": t, "fieldData": {f: 1.2345 for f in fields}})
            self._next = {"securityData": rows}
        elif request.kind == "HistoricalDataRequest":
            self._next = {"securityData": {"security": "EURUSD1M Curncy", "fieldData": [
                {"date": "2026-09-17", "PX_LAST": 1.1750, "SETTLE_DT": "2026-10-21"},
                {"date": "2026-09-18", "PX_LAST": 1.1760}]}}
        else:
            super().sendRequest(request)


def test_unverified_ticker_probes_ask_the_apps_own_tickers_and_print_what_came_back(capsys):
    tool = _load_tool()
    rep, session = tool.Report(), _AnswerAllSession()
    tool.check_tenor_history(rep, _Blpapi, session, session)
    tool.check_ois_tickers(rep, _Blpapi, session, session)
    tool.check_vol_tickers(rep, _Blpapi, session, session)

    sent = {r.kind: [] for r in session.sent}
    for r in session.sent:
        sent[r.kind].append(r.getElement("securities").items)
    assert sent["ReferenceDataRequest"][0] == ["EESWE1 Curncy", "BPSWS1 Curncy", "JYSO1 Curncy",
                                               "SFSNT1 Curncy", "CDSO1 Curncy", "ADSO1 Curncy"]
    assert sent["ReferenceDataRequest"][1][:5] == ["EURUSDV1M BGN Curncy", "EURUSD25R1M BGN Curncy", "EURUSD25B1M BGN Curncy",
                                                   "EURUSD10R1M BGN Curncy", "EURUSD10B1M BGN Curncy"]
    hist = session.sent[0]
    assert hist.kind == "HistoricalDataRequest" and hist.getElement("securities").items == ["EURUSD1M Curncy"]
    assert hist.getElement("fields").items == ["PX_LAST", "SETTLE_DT"] and len(hist.scalars["startDate"]) == 8

    for name in ("tenorhist", "ois", "vol"):
        assert rep.checks[name]["required"] is False
    assert rep.checks["vol"]["ok"] is True and "10/10 answered PX_LAST" in rep.checks["vol"]["detail"]
    assert rep.checks["ois"]["ok"] is False                                   # one securityError, in Bloomberg's words
    assert "ADSO1 Curncy: nothing [securityError: Unknown/Invalid Security]" in rep.checks["ois"]["detail"]
    ten = rep.checks["tenorhist"]
    assert ten["ok"] is True and ten["rows_with_settle_dt"] == 1 and "2 row(s), 1 with SETTLE_DT" in ten["detail"]
    assert rep.all_required_ok is False                                       # still informational only
    out = capsys.readouterr().out
    assert "[CHECK ] ois" in out and "[OK    ] vol" in out


def test_the_macro_probes_are_gone():
    """Phase 2 of the commodity conversion (user, 2026-09-24): no NDF, listed index option or
    overnight fixing step is asked of Bloomberg any more."""
    tool = _load_tool()
    for name in ("check_ndf_1m", "check_listed_option", "listed_option_tickers", "NDF_1M_TICKERS"):
        assert not hasattr(tool, name), name
    assert not any(t.endswith(" Index") for t in tool.OIS_PROBE_TICKERS)
