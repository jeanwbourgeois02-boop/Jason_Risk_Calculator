"""tools/bloomberg_diagnostic.py runs standalone; without Bloomberg it must report
unavailability cleanly (exit 1, both reports written, no fake prices, DB untouched)."""
import importlib.util
import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "HA_PNL_20260818.csv"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bbg_diag", REPO / "tools" / "bloomberg_diagnostic.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_db(path: Path) -> None:
    from data.ingest import schema
    conn = schema.connect(path)
    conn.execute("INSERT INTO instruments VALUES ('AUDUSD','FX','AUD','USD',1,0,'AUDUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('a1','BNP','AUDUSD','FX_FWD','a1','2026-08-10',-1e6,0.65,'acc','cp','HAHY7','t','d')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-16", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-16", 0.65, 1)])
    conn.execute("INSERT INTO positions VALUES ('2026-08-17','BNP','acc','AUDUSD','2026-09-16',-1e6,650000,0.66,1,0,0,0,0,0)")
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
    assert led["required"] is False and led["ok"] is False and led["snapshots"] == 0
    assert "no snapshot yet" in led["detail"] and "0 realised trade(s)" in led["detail"]
    feed = data["checks"]["feed"]
    assert feed["required"] is False and feed["ok"] is True and "skipped=1" in feed["detail"] and "ledger:" in feed["detail"]
    txt = next((tmp_path / "reports").glob("*.txt")).read_text(encoding="utf-8")
    assert "Informational (not required for exit 0)" in txt and "[CHECK ] ledger" in txt
    assert "[CHECK ] ledger" in capsys.readouterr().out
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM pnl_snapshots").fetchone()[0] == 0   # read-only


def test_missing_database_is_reported_not_raised(tmp_path, capsys):
    tool = _load_tool()
    code = tool.main(["--once", "--db", str(tmp_path / "nope.db"), "--out", str(tmp_path / "r"), "--port", "1"])
    assert code == 1
    data = json.loads(next((tmp_path / "r").glob("*.json")).read_text(encoding="utf-8"))
    assert data["checks"]["database"]["ok"] is False and "does not exist" in data["checks"]["database"]["detail"]
