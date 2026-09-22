"""data/bloomberg/snapshot.py: the marks snapshot that carries market data through git
from the Bloomberg PC to a PC with no Terminal. Pure sqlite."""
import sqlite3

import pytest

from data.bloomberg import snapshot
from data.ingest import schema

INSTRUMENT_COLS = ("instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                   "bbg_ticker, expiry_date")
SNAP = "2026-09-14T15:00:00-04:00"
# Floats with no short decimal form: the CSV must give back the identical bits.
AWKWARD = 0.1 + 0.2
TINY = 1e-9 / 3


def _instruments(conn, rows):
    conn.executemany(f"INSERT INTO instruments ({INSTRUMENT_COLS}) VALUES (?,?,?,?,?,?,?,?)", rows)


def _marks(conn, rows):
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", rows)


def _bloomberg_pc(path):
    conn = schema.connect(path)
    _instruments(conn, [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDKRW", "FX", "USD", "KRW", 1, 1, "USDKRW Curncy", "9999-12-31"),
        ("CASH-CAD", "CASH", "CAD", "CAD", 1, 0, "", "9999-12-31"),  # no mark: stays behind
    ])
    _marks(conn, [
        ("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.62, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", AWKWARD, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-30", "FWD_OUTRIGHT", 0.71, "BBG_INTERP", SNAP),
        ("2026-09-14", "USDKRW", "2026-09-14", "NDF_1M", 1391.5, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-30", "DELTA", TINY, "QL_OPTIONS_PRICER", SNAP),
    ])
    conn.execute("INSERT INTO curves VALUES ('USD-SOFR-OIS','2026-09-14','2027-09-14',?,0.04,'QL_PRICER')",
                 (AWKWARD / 3,))
    conn.execute("INSERT INTO curve_quotes VALUES ('2026-09-14','USD','SOFR','1Y','USOSFR1 Curncy',"
                 "4.05,'OIS','PX_LAST','BBG_BDP')")
    conn.execute("INSERT INTO index_fixings VALUES ('SOFR','2026-09-11',0.0405,'BBG_BDH')")
    # the tables a pull creates itself (not in data/ingest/schema.py), as the pull leaves them
    from data.bloomberg.vol_marketdata import ensure_vol_quotes_table
    from data.bloomberg.rates_vol_marketdata import ensure_rate_vol_quotes_table
    from engine.options.equity_commodity import ensure_tables, set_dividend_yield
    ensure_vol_quotes_table(conn)
    ensure_rate_vol_quotes_table(conn)
    ensure_tables(conn)
    conn.execute("INSERT INTO vol_quotes (as_of_date, pair, tenor, quote_type, value, ticker, field, source, "
                 "snapped_at) VALUES ('2026-09-14','USDKRW','1M','ATM',9.25,'USDKRWV1M Curncy','PX_LAST',"
                 "'BBG_BDP',?)", (SNAP,))
    conn.execute("INSERT INTO rate_vol_quotes VALUES ('2026-09-14','USD','SOFR','SWAPTION','1Y','10Y','ATM',0.0,"
                 "0.85,'NORMAL','USSN0110 Curncy','PX_LAST','BBG_BDP',?)", (SNAP,))
    set_dividend_yield(conn, "2026-09-14", "SPX Index", 0.0125, source="BBG_BDP")
    conn.commit()
    (path.parent / (path.name + ".bloomberg_status.json")).write_text(
        '{"time": "2026-09-14T15:01:00-04:00", "connected": true, "requested": 9, "written": 8, "failed": 1}')
    return conn


def _dump(conn, table):
    return sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)


def test_round_trip_gives_the_other_pc_identical_market_data(tmp_path):
    pc = _bloomberg_pc(tmp_path / "pc.db")
    manifest = snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")
    assert manifest["rows"] == {"instruments": 2, "marks": 5, "curves": 1, "curve_quotes": 1,
                                "index_fixings": 1, "vol_quotes": 1, "rate_vol_quotes": 1,
                                "equity_dividend_yields": 1}
    assert (manifest["marks_from"], manifest["marks_through"]) == ("2026-09-09", "2026-09-14")
    assert set(manifest["ddl"]) == set(snapshot.MARKET_TABLES)
    assert manifest["ddl"]["vol_quotes"].startswith("CREATE TABLE vol_quotes")
    # the pull's own log travels too, for reading
    assert manifest["last_pull"] == {"time": "2026-09-14T15:01:00-04:00", "connected": True,
                                     "requested": 9, "written": 8, "failed": 1}
    assert (tmp_path / "snap" / snapshot.PULL_STATUS).exists()

    # the other PC's database has never created the pull's own tables: a fresh
    # schema.connect() has no vol_quotes / rate_vol_quotes / equity_dividend_yields
    schema.connect(tmp_path / "mac.db").close()
    assert not snapshot._table_info(sqlite3.connect(tmp_path / "mac.db"), "vol_quotes")
    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert out["rows"]["marks"] == 5 and out["instruments_added"] == 2 and out["skipped_marks"] == 0
    assert out["rows"]["vol_quotes"] == 1 and out["rows"]["equity_dividend_yields"] == 1
    assert out["manifest"] == manifest

    mac = sqlite3.connect(tmp_path / "mac.db")
    for table in snapshot.MARKET_TABLES:
        assert _dump(mac, table) == _dump(pc, table)  # exact floats, sources and snap times
    # the tables it created match the source's own columns
    assert [c[1] for c in snapshot._table_info(mac, "rate_vol_quotes")] == [c[1] for c in snapshot._table_info(pc, "rate_vol_quotes")]
    # this PC's own pull status is not touched: the log is read from the snapshot folder only
    assert not (tmp_path / "mac.db.bloomberg_status.json").exists()
    assert [r[0] for r in mac.execute("SELECT instrument_id FROM instruments ORDER BY 1")] == ["AUDUSD", "USDKRW"]
    # is_ndf came back an integer, not the text '1'
    assert mac.execute("SELECT typeof(is_ndf), is_ndf FROM instruments WHERE instrument_id='USDKRW'").fetchone() == ("integer", 1)


def test_export_is_byte_stable_so_a_second_export_of_the_same_marks_commits_nothing(tmp_path):
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "a")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "b")
    for table in ("instruments",) + snapshot.MARKET_TABLES:
        data = (tmp_path / "a" / f"{table}.csv").read_bytes()
        assert data.count(b"\n") >= 2, table  # header and at least one row: every table is exported
        assert data == (tmp_path / "b" / f"{table}.csv").read_bytes()
        assert b"\r" not in data  # '\n' on every OS
    assert not list((tmp_path / "a").glob("*.tmp"))

    # exporting over an unchanged snapshot keeps its manifest, export time included
    manifest = tmp_path / "a" / snapshot.MANIFEST
    manifest.write_text(manifest.read_text().replace('"exported_at": "2', '"exported_at": "1'))
    kept = manifest.read_bytes()
    assert snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "a")["exported_at"].startswith("1")
    assert manifest.read_bytes() == kept


def test_import_mirrors_the_bloomberg_pc_but_keeps_manual_marks_and_local_instruments(tmp_path):
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")

    mac = schema.connect(tmp_path / "mac.db")
    _instruments(mac, [("AUDUSD", "FX", "AUD", "USD", 1, 0, "LOCAL TICKER", "9999-12-31")])
    _marks(mac, [
        # a last live press the Bloomberg PC's backfill has since replaced under another source:
        # left on, this direct quote would beat the snapshot's BBG_INTERP row in marks_official
        ("2026-09-14", "AUDUSD", "2026-09-30", "FWD_OUTRIGHT", 0.99, "BBG_BFXFORWARD", SNAP),
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", 0.5, "MANUAL", SNAP),
    ])
    mac.commit()
    mac.close()

    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert out["dropped"]["marks"] == 1 and out["instruments_added"] == 1
    assert out["dropped"]["equity_dividend_yields"] == 0  # the table did not exist here: created, nothing dropped

    mac = sqlite3.connect(tmp_path / "mac.db")
    official = mac.execute("SELECT value, source FROM marks_official WHERE mark_type='FWD_OUTRIGHT'").fetchall()
    assert official == [(0.71, "BBG_INTERP")]
    assert mac.execute("SELECT value FROM marks WHERE source='MANUAL'").fetchall() == [(0.5,)]
    assert mac.execute("SELECT bbg_ticker FROM instruments WHERE instrument_id='AUDUSD'").fetchone() == ("LOCAL TICKER",)

    # importing the same snapshot again changes nothing
    before = _dump(mac, "marks")
    mac.close()
    snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert _dump(sqlite3.connect(tmp_path / "mac.db"), "marks") == before


def test_import_freezes_the_settled_trades_because_no_pull_runs_without_bloomberg(tmp_path):
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")

    mac = schema.connect(tmp_path / "mac.db")
    _instruments(mac, [("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31")])
    # sold 1m AUD @0.65 settling 09-10: frozen at the snapshot's 09-09 spot, 0.62
    mac.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "", "t", "d", ""))
    mac.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-10", 0.65, 1),
    ])
    mac.commit()
    mac.close()

    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    assert out["ledger"]["realised"] == 1
    row = sqlite3.connect(tmp_path / "mac.db").execute(
        "SELECT pnl_usd, spot_as_of_date FROM realised_pnl WHERE trade_id='a1'").fetchone()
    assert row == (pytest.approx(30000.0), "2026-09-09")


def test_nothing_to_export_and_nothing_to_import_say_so_and_touch_nothing(tmp_path):
    schema.connect(tmp_path / "empty.db").close()
    with pytest.raises(snapshot.SnapshotError, match="no marks on file"):
        snapshot.export_snapshot(tmp_path / "empty.db", tmp_path / "snap")
    assert not (tmp_path / "snap").exists()
    with pytest.raises(snapshot.SnapshotError, match="no database"):
        snapshot.export_snapshot(tmp_path / "absent.db", tmp_path / "snap")

    with pytest.raises(snapshot.SnapshotError, match="no snapshot"):
        snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap")
    assert not (tmp_path / "mac.db").exists()


def test_a_stored_value_that_is_not_a_number_travels_as_the_data_error_it_is(tmp_path):
    pc = _bloomberg_pc(tmp_path / "pc.db")
    _marks(pc, [("2026-09-14", "AUDUSD", "2026-10-30", "FWD_OUTRIGHT", "n/a", "BBG_BFXFORWARD", SNAP)])
    pc.commit()
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")
    snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    got = sqlite3.connect(tmp_path / "mac.db").execute(
        "SELECT typeof(value), value FROM marks WHERE settle_date='2026-10-30'").fetchone()
    assert got == ("text", "n/a")


# =========================================================================== 2026-09-22: every pull saves
def _git_repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"], ["commit", "-q", "--allow-empty", "-m", "root"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "stray.txt").write_text("not part of the snapshot")
    return repo


def _log(repo):
    import subprocess
    return subprocess.run(["git", "log", "--format=%s", "--", "data/bbg_snapshot"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.splitlines()


def test_save_after_pull_only_writes_the_files_unless_asked_to_commit(tmp_path, monkeypatch):
    """User, 2026-09-22: "every pull from bbg triggers the saving" and then "dont need to
    trigger commit and push, just need to make sure the bbg data is logged and stored
    locally, I will trigger the commit and push myself": the pull's own call writes the
    folder and commits nothing; the files sit in the working copy for the user's commit."""
    import subprocess
    monkeypatch.delenv("RISK_SNAPSHOT", raising=False)
    repo = _git_repo(tmp_path)
    _bloomberg_pc(tmp_path / "pc.db").close()
    lines = []
    out = snapshot.save_after_pull(tmp_path / "pc.db", repo_root=repo, log=lines.append)
    assert out == {"exported": True, "committed": False, "pushed": False,
                   "message": "marks snapshot: 5 marks through 2026-09-14 written to data/bbg_snapshot/ (commit and push it yourself)"}
    assert lines == [out["message"]] and _log(repo) == []
    status = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=repo, capture_output=True, text=True).stdout
    assert "?? data/bbg_snapshot/marks.csv" in status and "?? stray.txt" in status   # written, untracked, for the user's commit


def test_save_after_pull_with_commit_commits_the_snapshot_folder_alone_and_says_so(tmp_path, monkeypatch):
    """marks-export's path: the export, then a commit naming data/bbg_snapshot only (a stray
    file on that PC is left out), then the push (off here: no remote). An identical second
    save commits nothing."""
    import subprocess
    monkeypatch.delenv("RISK_SNAPSHOT", raising=False)
    repo = _git_repo(tmp_path)
    _bloomberg_pc(tmp_path / "pc.db").close()
    lines = []
    out = snapshot.save_after_pull(tmp_path / "pc.db", repo_root=repo, commit=True, push=False, log=lines.append)
    assert (out["exported"], out["committed"], out["pushed"]) == (True, True, False)
    assert out["message"].startswith("marks snapshot: 5 marks through 2026-09-14 written to data/bbg_snapshot/; committed")
    assert lines == [out["message"]]
    assert _log(repo) == ["Bloomberg marks snapshot " + snapshot.read_manifest(repo / "data/bbg_snapshot")["exported_at"]
                          + ": marks through 2026-09-14"]
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert status.strip() == "?? stray.txt"                      # the stray file was never added
    again = snapshot.save_after_pull(tmp_path / "pc.db", repo_root=repo, commit=True, push=False, log=lines.append)
    assert again["committed"] is False and "identical: nothing to commit" in again["message"]
    assert len(_log(repo)) == 1


def test_save_after_pull_never_raises_and_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.delenv("RISK_SNAPSHOT", raising=False)
    repo = _git_repo(tmp_path)
    schema.connect(tmp_path / "empty.db").close()
    out = snapshot.save_after_pull(tmp_path / "empty.db", repo_root=repo, push=False, log=lambda s: None)
    assert out == {"exported": False, "committed": False, "pushed": False,
                   "message": "marks snapshot: no marks on file: nothing to export (press Pull Bloomberg now first)"}
    _bloomberg_pc(tmp_path / "pc.db").close()
    plain = tmp_path / "plain"; plain.mkdir()
    out = snapshot.save_after_pull(tmp_path / "pc.db", repo_root=plain, commit=True, push=False, log=lambda s: None)
    assert out["exported"] and not out["committed"] and "not a git clone" in out["message"]
    monkeypatch.setenv("RISK_SNAPSHOT", "0")
    assert snapshot.save_after_pull(tmp_path / "pc.db", repo_root=repo, push=False, log=lambda s: None)["message"] == \
        "marks snapshot off (RISK_SNAPSHOT=0)"
    assert _log(repo) == []


def test_the_background_backfill_saves_only_after_a_real_pull(tmp_path, monkeypatch):
    """A test's or a developer's fake fetches must never write or commit the repository's
    own data/bbg_snapshot/: the save runs only when start_auto_backfill was given no fakes
    (a real Bloomberg session), i.e. from the feed's own cycle."""
    from data.bloomberg import backfill
    calls = []
    monkeypatch.setattr(snapshot, "save_after_pull", lambda db_path, **kw: calls.append(db_path) or {"message": "saved"})
    monkeypatch.setattr(backfill, "auto_backfill", lambda *a, **k: [])
    p = tmp_path / "risk.db"
    schema.connect(p).close()
    t = backfill.start_auto_backfill(p, fetch=lambda *a, **k: {}, fwd_fetch=lambda *a, **k: {}, fut_fetch=lambda *a, **k: {})
    if t is not None:
        t.join(5)
    assert calls == []                                            # fakes: no save
    published = []
    monkeypatch.setattr("data.bloomberg.live.availability", lambda host, port: (True, ""))
    monkeypatch.setattr("data.bloomberg.live.patch_status", lambda db, key, block: published.append(dict(block)))
    t = backfill.start_auto_backfill(p)                           # a real pull: no fakes given
    assert t is not None
    t.join(5)
    assert calls == [p] and any(b.get("snapshot") == "saved" for b in published)
    # a backfill that raises must not lose the pull's own marks: the save still runs
    def boom(*a, **k):
        raise RuntimeError("history down")
    monkeypatch.setattr(backfill, "auto_backfill", boom)
    calls.clear(); published.clear()
    t = backfill.start_auto_backfill(p)
    t.join(5)
    assert calls == [p]
    assert any(b.get("snapshot") == "saved" for b in published) and any("history down" in b.get("reason", "") for b in published)


def test_import_freezes_as_of_the_book_date_not_the_machines_date(tmp_path, monkeypatch):
    """The import's closing freeze (no pull runs on this PC) is as of the book date
    (data.bloomberg.live.book_today: New York, rolled at 17:00 New York; user decision
    2026-09-22), never the machine's local date -- a Hong Kong PC is a day ahead of New York
    until early afternoon. The machine's date here is after a1's 09-10 settlement, so only
    a book date before it can leave the trade unfrozen."""
    from datetime import date
    from data.bloomberg import live
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")
    mac = schema.connect(tmp_path / "mac.db")
    _instruments(mac, [("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31")])
    mac.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "", "t", "d", ""))
    mac.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-10", 0.65, 1),
    ])
    mac.commit()
    mac.close()
    monkeypatch.setattr(live, "book_today", lambda now=None: date(2026, 9, 9))       # the book is still before settlement
    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap")            # no as_of: the book date
    assert out["ledger"]["realised"] == 0
    monkeypatch.setattr(live, "book_today", lambda now=None: date(2026, 9, 14))
    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap")
    assert out["ledger"]["realised"] == 1
    assert sqlite3.connect(tmp_path / "mac.db").execute(
        "SELECT spot_as_of_date FROM realised_pnl WHERE trade_id='a1'").fetchone() == ("2026-09-09",)


# =========================================================================== 2026-09-22: the import passes the ledger's re-freeze through
def test_import_returns_the_ledgers_refrozen_and_kept(tmp_path, monkeypatch):
    import engine.pnl.ledger as ledger_mod
    _bloomberg_pc(tmp_path / "pc.db")
    snapshot.export_snapshot(tmp_path / "pc.db", tmp_path / "snap")
    schema.connect(tmp_path / "mac.db").close()
    new = {"realised": 1, "unrealisable": [], "repaired": [],
           "refrozen": [{"trade_id": "a1", "product": "FX_FWD", "mark_type": "SPOT", "spot_as_of_date": "2026-09-09",
                         "pnl_from": 1.0, "pnl_to": 2.0, "why": "the imported close replaced a live row"}],
           "kept": [{"trade_id": "z9", "product": "FX_OPTION", "reason": "no close-out spot on file yet"}]}
    monkeypatch.setattr(ledger_mod, "realise_settled", lambda c, as_of, **kw: dict(new))
    out = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")
    led = out["ledger"]
    assert led["as_of_date"] == "2026-09-14" and led["realised"] == 1
    assert led["refrozen"] == new["refrozen"] and led["kept"] == new["kept"]
    assert led["refrozen_count"] == 1 and led["refrozen_summary"] == "1 settled trade re-frozen at the close"
    # an older ledger's bare ids
    monkeypatch.setattr(ledger_mod, "realise_settled", lambda c, as_of, **kw: {"realised": 0, "unrealisable": [], "refrozen": ["a1"]})
    led = snapshot.import_snapshot(tmp_path / "mac.db", tmp_path / "snap", as_of="2026-09-14")["ledger"]
    assert led["refrozen"] == [{"trade_id": "a1"}] and led["kept"] == [] and led["refrozen_count"] == 1
