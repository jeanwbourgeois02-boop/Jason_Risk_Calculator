"""Tests for 2_launcher.py, the single setup / start / doctor entry point.

The module filename starts with a digit, so it cannot be `import`ed by name (not a valid
identifier); it is loaded by file path instead, under the local name `risk` so the rest of
this file reads exactly as it would for a normally-named module."""
import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_launcher():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("risk", root / "2_launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


risk = _load_launcher()
from collections import namedtuple
_VersionInfo = namedtuple("_VersionInfo", "major minor micro")


def test_parser_has_exactly_the_documented_commands():
    parser = risk.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert set(sub.choices) == {"setup", "start", "doctor", "_load_sample", "freeze",
                                "marks-export", "marks-import", "reprice", "health",
                                "bbg-check", "contracts-apply"}


def test_import_checks_are_a_subset_of_installed_packages():
    """Every module doctor/setup import-check must actually come from something PACKAGES
    installs (or be stdlib), so the two lists in 2_launcher.py can't drift apart silently."""
    pip_names = {spec.split(">")[0].split("=")[0].split("<")[0].strip().lower() for spec in risk.PACKAGES}
    # import name -> pip distribution name, for the handful that differ
    import_to_dist = {"yaml": "pyyaml", "zoneinfo": None}  # zoneinfo is stdlib, not pip-installed
    for mod in risk.IMPORT_CHECKS:
        mod = mod.split(".")[0]                # 'scipy.optimize' is installed by 'scipy'
        dist = import_to_dist.get(mod, mod)
        if dist is None:
            continue
        assert dist.lower() in pip_names, f"{mod} is import-checked but not in PACKAGES"


def test_freeze_writes_requirements_from_packages(tmp_path, monkeypatch):
    target = tmp_path / "requirements.txt"
    monkeypatch.setattr(risk, "REQUIREMENTS", target)
    assert risk.cmd_freeze(risk.build_parser().parse_args(["freeze"])) == 0
    text = target.read_text(encoding="utf-8")
    for spec in risk.PACKAGES + risk.DEV_PACKAGES:
        assert spec in text
    assert "GENERATED" in text


def test_setup_refuses_old_python(monkeypatch, capsys):
    monkeypatch.setattr(risk.sys, "version_info", _VersionInfo(3, 8, 0))
    args = risk.build_parser().parse_args(["setup"])
    assert risk.cmd_setup(args) == 1
    assert "3.11+" in capsys.readouterr().out


def test_start_reexecs_in_venv_when_outside(monkeypatch):
    monkeypatch.setattr(risk, "in_venv", lambda: False)
    monkeypatch.setattr(risk, "sync_with_github", lambda: (False, "code is current with GitHub (test)"))
    monkeypatch.setattr(risk, "venv_imports_ok", lambda: True)
    monkeypatch.setattr(risk, "terminal_installed", lambda: False)   # not a Bloomberg PC: no blpapi step
    monkeypatch.setattr(risk, "tcp_open", lambda *a, **k: False)
    monkeypatch.setattr(risk.VENV_PY.__class__, "exists", lambda self: True)
    calls = []
    monkeypatch.setattr(risk.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 7)
    monkeypatch.setattr(risk.sys, "argv", ["2_launcher.py", "start", "--force-new"])
    assert risk.cmd_start(risk.build_parser().parse_args(["start", "--force-new"])) == 7
    assert calls[0][0] == str(risk.VENV_PY) and calls[0][-2:] == ["start", "--force-new"]


def test_start_without_venv_runs_setup_first(monkeypatch, capsys):
    """2026-09-17: a PC with no .venv used to stop with 'No .venv yet'; start now runs
    setup itself (skipping the tests) and only gives up if that setup fails."""
    monkeypatch.setattr(risk, "in_venv", lambda: False)
    monkeypatch.setattr(risk, "sync_with_github", lambda: (False, "code is current with GitHub (test)"))
    monkeypatch.setattr(risk, "venv_imports_ok", lambda: True)
    monkeypatch.setattr(risk.VENV_PY.__class__, "exists", lambda self: False)
    ran = []
    monkeypatch.setattr(risk, "cmd_setup", lambda a: ran.append(a.skip_tests) or 5)
    assert risk.cmd_start(risk.build_parser().parse_args(["start"])) == 5
    assert ran == [True]
    assert "running setup first" in capsys.readouterr().out


def test_start_inside_venv_calls_launcher(monkeypatch):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    launched = Mock(return_value=0)
    monkeypatch.setattr("ui.launch.main", launched)
    assert risk.cmd_start(risk.build_parser().parse_args(["start", "--force-new"])) == 0
    launched.assert_called_once_with(["--force-new"])


def _doctor(monkeypatch, tmp_path, db=None):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr(risk, "tcp_open", lambda *a, **k: False)
    monkeypatch.setattr(risk, "terminal_installed", lambda: False)   # the dev PC has C:\blp but no Terminal login
    monkeypatch.setattr("ui.launch.probe", lambda url: None)
    monkeypatch.setenv("RISK_DB", str(db or tmp_path / "risk.db"))
    d = risk.Doctor()
    risk.doctor_checks(d, bloomberg=False, git=False)
    return d


def test_doctor_reports_missing_database(monkeypatch, tmp_path):
    d = _doctor(monkeypatch, tmp_path)
    failed = {name: fix for name, ok, _, fix in d.rows if ok is False}
    assert "database" in failed and "setup" in failed["database"]


def test_doctor_passes_on_schema_database(monkeypatch, tmp_path):
    from data.ingest import schema
    db = tmp_path / "risk.db"
    conn = sqlite3.connect(db)
    schema.create_schema(conn)
    conn.close()
    d = _doctor(monkeypatch, tmp_path, db)
    by_name = {name: ok for name, ok, _, _ in d.rows}
    assert by_name["database"] is True
    assert by_name["packages"] is True
    assert by_name["data"] is None  # empty database is information, not a failure
    assert [r for r in d.failures if r[0] != "status file"] == []


def test_doctor_flags_stale_running_instance(monkeypatch, tmp_path):
    from ui import launch
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr(risk, "tcp_open", lambda *a, **k: False)
    monkeypatch.setenv("RISK_DB", str(tmp_path / "risk.db"))
    monkeypatch.setattr("ui.launch.probe",
                        lambda url: launch.identity("0000deadbeef0000") if url.endswith("8050") else None)
    d = risk.Doctor()
    risk.doctor_checks(d, bloomberg=False, git=False)
    assert any(name == "stale app" and ok is False for name, ok, _, _ in d.rows)


def test_doctor_bloomberg_mode_requires_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr(risk, "tcp_open", lambda *a, **k: False)
    monkeypatch.setattr("ui.launch.probe", lambda url: None)
    monkeypatch.setenv("RISK_DB", str(tmp_path / "risk.db"))
    d = risk.Doctor()
    risk.doctor_checks(d, bloomberg=True, git=False)
    names = {name for name, ok, _, _ in d.rows if ok is False}
    assert "terminal" in names


def test_root_bloomberg_diagnostics_is_a_thin_wrapper(monkeypatch):
    """3_diagnostic.py is the third root-level file: it must exist at repo root,
    do nothing but dispatch to tools.bbg_diagnostics.main (the one real implementation
    also used by ui/tabs/header.py via the data/bloomberg shim), and never re-implement
    any check itself."""
    root = Path(risk.__file__).resolve().parent
    entry = root / "3_diagnostic.py"
    assert entry.exists()
    source = entry.read_text(encoding="utf-8")
    assert "from tools.bbg_diagnostics import main" in source

    called = Mock(return_value=0)
    monkeypatch.setattr("tools.bbg_diagnostics.main", called)
    spec = __import__("importlib.util", fromlist=["util"]).spec_from_file_location(
        "root_bloomberg_diagnostics", entry)
    mod = __import__("importlib.util", fromlist=["util"]).module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main is called


def test_doctor_fails_when_a_terminal_is_present_but_blpapi_is_missing(monkeypatch, tmp_path):
    """2026-09-18 audit: plain `doctor` used to report blpapi as information only, so a
    Bloomberg PC whose .venv lacks blpapi (setup ran before the Terminal was installed or
    logged in) got "Everything checks out" while the live feed could never start."""
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr(risk, "tcp_open", lambda *a, **k: True)      # a Terminal answers on 8194 ...
    monkeypatch.setitem(sys.modules, "blpapi", None)                 # ... but blpapi does not import
    monkeypatch.setattr("ui.launch.probe", lambda url: None)
    monkeypatch.setenv("RISK_DB", str(tmp_path / "risk.db"))
    d = risk.Doctor()
    risk.doctor_checks(d, bloomberg=False, git=False)
    failed = {name: fix for name, ok, _, fix in d.rows if ok is False}
    assert "blpapi" in failed and "--bloomberg" in failed["blpapi"]


def _no_audit(*a, **k):
    raise OSError("audit not run in this test")


# ----------------------------------------------------------------------------- reprice

def _reprice(monkeypatch, tmp_path, argv, options_result=None):
    """Run `reprice` with the options recalc faked (no pricing, no Bloomberg): returns
    (exit code, calls) where calls is [(pricer, as_of, since), ...] in call order. The swaps
    left the app with the rates book (commodity conversion Phase 2, user 2026-09-24): the
    options are the only thing re-priced."""
    from data.ingest import schema
    db = tmp_path / "risk.db"
    schema.connect(db).close()
    monkeypatch.setenv("RISK_DB", str(db))
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr("data.bloomberg.live.book_today", lambda now=None: __import__("datetime").date(2026, 9, 22))
    calls = []

    def fake_options(conn, as_of, since=None):
        calls.append(("options", as_of, since))
        return options_result or {"as_of": as_of, "since": since, "days": [
            {"day": "2026-09-21", "priced": 3, "skipped": [{"trade_id": "T1", "reason": "no smile"}]}],
            "priced": 3, "skipped": 1}
    monkeypatch.setattr("engine.options.store.recalc_on_file", fake_options)
    return risk.main(["reprice", *argv]), calls


def test_reprice_runs_the_options_only_on_the_book_date(monkeypatch, tmp_path, capsys):
    code, calls = _reprice(monkeypatch, tmp_path, [])
    assert code == 0
    assert calls == [("options", "2026-09-22", None)]
    out = capsys.readouterr().out
    assert "options  2026-09-21  priced 3  skipped 1" in out
    assert "options: 3 priced, 1 skipped over 1 day(s)" in out
    assert "rates" not in out and "swap" not in out


def test_reprice_passes_as_of_and_since_to_the_options_recalc(monkeypatch, tmp_path):
    code, calls = _reprice(monkeypatch, tmp_path, ["--as-of", "2026-09-18", "--since", "2026-09-15"])
    assert code == 0
    assert calls == [("options", "2026-09-18", "2026-09-15")]


def test_reprice_exits_1_on_a_recalc_error(monkeypatch, tmp_path, capsys):
    code, calls = _reprice(monkeypatch, tmp_path, [], options_result={
        "as_of": "2026-09-22", "since": None, "days": [], "priced": 0, "skipped": 0,
        "error": "RuntimeError('vol surface')"})
    assert code == 1
    assert [c[0] for c in calls] == ["options"]
    assert "options: error RuntimeError('vol surface')" in capsys.readouterr().out


def test_reprice_help_names_the_options_only():
    parser = risk.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    helps = {c.dest: c.help for c in sub._choices_actions}
    assert "FX options" in helps["reprice"] and "swap" not in helps["reprice"]


def test_reprice_refuses_without_a_database(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RISK_DB", str(tmp_path / "missing.db"))
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    assert risk.main(["reprice"]) == 1
    assert "no database" in capsys.readouterr().out


# ----------------------------------------------------------------------------- bbg-check

def _bbg_check(monkeypatch, argv, code=0):
    """Run `bbg-check` with the subprocess faked: returns (exit code, the command run)."""
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    seen = []

    def fake_run(cmd, check=True, **kw):
        seen.append([str(c) for c in cmd])
        assert check is False                     # the check's own exit code is the answer, never raised
        return code
    monkeypatch.setattr(risk, "run", fake_run)
    return risk.main(["bbg-check", *argv]), seen


def test_bbg_check_runs_the_ticker_check_module_inside_the_venv_with_no_flags(monkeypatch):
    code, seen = _bbg_check(monkeypatch, [])
    assert code == 0
    assert seen == [[sys.executable, "-m", "data.bloomberg.ticker_check"]]


def test_bbg_check_passes_every_flag_through(monkeypatch):
    code, seen = _bbg_check(monkeypatch, [
        "--dry-run", "--root", "NYMEX:CL", "--root", "CBOT:C", "--sector", "energy", "--book",
        "--db", "x.db", "--search", "--host", "bbg-pc", "--port", "8195", "--out", "rep", "--limit", "5"])
    assert code == 0
    passed = seen[0][3:]
    assert passed[:3] == ["--dry-run", "--book", "--search"]
    assert passed[3:] == ["--root", "NYMEX:CL", "--root", "CBOT:C", "--sector", "energy", "--db", "x.db",
                          "--host", "bbg-pc", "--port", "8195", "--out", "rep", "--limit", "5"]


def test_bbg_check_flags_are_the_ticker_checks_own():
    """Every flag the launcher passes is one the ticker check's parser knows, and none of its
    flags is missing from the launcher."""
    from data.bloomberg import ticker_check
    theirs = {s for a in ticker_check._parser()._actions for s in a.option_strings if s not in ("-h", "--help")}
    ours = ({f for _, f in risk.BBG_CHECK_FLAGS} | {f for _, f in risk.BBG_CHECK_OPTIONS} | {"--root"})
    assert ours == theirs


def test_bbg_check_returns_the_checks_exit_code(monkeypatch):
    for expected in (0, 1, 2):
        code, _ = _bbg_check(monkeypatch, ["--limit", "1"], code=expected)
        assert code == expected


def test_bbg_check_reexecs_in_the_venv_when_outside(monkeypatch):
    monkeypatch.setattr(risk, "in_venv", lambda: False)
    monkeypatch.setattr(risk, "VENV_PY", Path(sys.executable))       # "exists"
    monkeypatch.setattr(risk.sys, "argv", ["2_launcher.py", "bbg-check", "--dry-run"])
    seen = []
    monkeypatch.setattr(risk, "reexec_in_venv", lambda argv: seen.append(argv) or 7)
    assert risk.main(["bbg-check", "--dry-run"]) == 7
    assert seen == [["bbg-check", "--dry-run"]]


def test_doctor_ticker_check_exit_2_is_a_failure_and_1_is_information(monkeypatch):
    for code, expected in ((0, True), (1, None), (2, False)):
        monkeypatch.setattr(risk, "run", lambda cmd, check=True, _c=code, **kw: _c)
        d = risk.Doctor()
        assert risk.doctor_ticker_check(d) == code
        (name, ok, detail, fix), = d.rows
        assert name == "bbg tickers" and ok is expected
        if code == 2:
            assert d.failures and "bbg-check" in fix
        if code == 1:
            assert "contracts-apply" in detail and not d.failures


def test_doctor_bloomberg_runs_the_ticker_check_after_the_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr(risk, "doctor_checks", lambda d, bloomberg, git=True: None)
    monkeypatch.setattr("tools.health.collect", _no_audit)
    cmds = []

    def fake_run(cmd, check=True, **kw):
        cmds.append([str(c) for c in cmd])
        return 2 if "data.bloomberg.ticker_check" in cmds[-1] else 0
    monkeypatch.setattr(risk, "run", fake_run)
    assert risk.main(["doctor", "--bloomberg", "--no-git"]) == 1          # exit 2 of the check fails doctor
    assert cmds[0][1].endswith("bloomberg_terminal_probe.py")
    assert cmds[1] == [sys.executable, "-m", "data.bloomberg.ticker_check"]


def test_plain_doctor_asks_bloomberg_nothing(monkeypatch):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr(risk, "doctor_checks", lambda d, bloomberg, git=True: None)
    monkeypatch.setattr("tools.health.collect", _no_audit)
    cmds = []
    monkeypatch.setattr(risk, "run", lambda cmd, check=True, **kw: cmds.append(cmd) or 0)
    assert risk.main(["doctor", "--no-git"]) == 0
    assert cmds == []


# ----------------------------------------------------------------------------- contracts-apply

def _contracts_apply(monkeypatch, tmp_path, argv, result=None, raises=None):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    ws = tmp_path / "contract_fixes_20260924.csv"
    ws.write_text("root_id,field,current,suggested,verdict,reason,apply\n", encoding="utf-8")
    calls = []

    def fake_apply(path, *, contracts_path=None, dry_run=False):
        calls.append((Path(path), dry_run))
        if raises:
            raise raises
        return result
    monkeypatch.setattr("data.contracts.apply_fixes", fake_apply)
    return risk.main(["contracts-apply", str(ws), *argv]), calls, ws


_APPLIED = {"row": 2, "root_id": "CBOT:C", "field": "price_scale", "before": "1", "after": "0.01",
            "multiplier_before": "5000", "multiplier_after": "50"}
_SKIPPED = {"row": 3, "root_id": "NYMEX:CL", "field": "bbg_root", "apply": ""}
_REFUSED = {"row": 4, "root_id": "ICE:ZZ", "field": "bbg_root", "why": "unknown contract root 'ICE:ZZ'"}


def test_contracts_apply_prints_every_row_in_plain_words_and_exits_0_when_nothing_refused(monkeypatch, tmp_path,
                                                                                          capsys):
    code, calls, ws = _contracts_apply(monkeypatch, tmp_path, [], result={
        "applied": [_APPLIED], "skipped": [_SKIPPED], "refused": [], "written": True})
    assert code == 0
    assert calls == [(ws, False)]
    out = capsys.readouterr().out
    assert "applied  row 2: CBOT:C price_scale '1' -> '0.01', multiplier 5000 -> 50" in out
    assert "skipped  row 3: NYMEX:CL bbg_root (apply is '', not yes)" in out
    assert "1 applied, 1 skipped, 0 refused" in out
    assert "config/contracts.csv rewritten" in out


def test_contracts_apply_exits_1_when_a_row_is_refused(monkeypatch, tmp_path, capsys):
    code, _, _ = _contracts_apply(monkeypatch, tmp_path, [], result={
        "applied": [], "skipped": [], "refused": [_REFUSED], "written": False})
    assert code == 1
    out = capsys.readouterr().out
    assert "refused  row 4: ICE:ZZ bbg_root: unknown contract root 'ICE:ZZ'" in out
    assert "config/contracts.csv not written" in out


def test_contracts_apply_dry_run_is_passed_and_says_nothing_was_written(monkeypatch, tmp_path, capsys):
    code, calls, ws = _contracts_apply(monkeypatch, tmp_path, ["--dry-run"], result={
        "applied": [_APPLIED], "skipped": [], "refused": [], "written": False})
    assert code == 0
    assert calls == [(ws, True)]
    assert "dry run: config/contracts.csv not written" in capsys.readouterr().out


def test_contracts_apply_a_bad_worksheet_is_reported_not_raised(monkeypatch, tmp_path, capsys):
    code, _, _ = _contracts_apply(monkeypatch, tmp_path, [], raises=ValueError("worksheet lacks column 'apply'"))
    assert code == 1
    assert "lacks column 'apply'" in capsys.readouterr().out


def test_contracts_apply_a_missing_worksheet_is_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    assert risk.main(["contracts-apply", str(tmp_path / "nope.csv")]) == 1
    assert "no worksheet at" in capsys.readouterr().out


def test_contracts_apply_end_to_end_on_a_copy_of_the_contract_file(monkeypatch, tmp_path, capsys):
    """The real apply_fixes on a copy of config/contracts.csv: a dry run writes nothing, and a
    row whose `current` is stale is refused (exit 1)."""
    import csv
    import shutil
    from data.contracts import fixes
    target = tmp_path / "contracts.csv"
    shutil.copyfile(Path(risk.__file__).resolve().parent / "config" / "contracts.csv", target)
    with open(target, encoding="utf-8-sig", newline="") as fh:
        first = next(csv.DictReader(fh))
    before = target.read_bytes()
    ws = tmp_path / "ws.csv"
    ws.write_text("root_id,field,current,suggested,verdict,reason,apply\n"
                  f"{first['root_id']},bbg_verified,{first['bbg_verified']},true,OK,,yes\n"
                  f"{first['root_id']},bbg_root,NOT-THE-CURRENT,XX,NOT_FOUND,,yes\n", encoding="utf-8")
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    real = fixes.apply_fixes
    monkeypatch.setattr("data.contracts.apply_fixes",
                        lambda path, dry_run=False: real(path, contracts_path=target, dry_run=dry_run))
    assert risk.main(["contracts-apply", str(ws), "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "stale worksheet" in out and "applied  row 2:" in out
    assert target.read_bytes() == before
