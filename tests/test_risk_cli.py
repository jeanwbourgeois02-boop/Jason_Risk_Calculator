"""Tests for risk.py, the single setup / start / doctor entry point."""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import risk  # noqa: E402
from collections import namedtuple
_VersionInfo = namedtuple("_VersionInfo", "major minor micro")


def test_parser_has_exactly_the_documented_commands():
    parser = risk.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert set(sub.choices) == {"setup", "start", "doctor", "backfill", "_load_sample"}


def test_setup_refuses_old_python(monkeypatch, capsys):
    monkeypatch.setattr(risk.sys, "version_info", _VersionInfo(3, 8, 0))
    args = risk.build_parser().parse_args(["setup"])
    assert risk.cmd_setup(args) == 1
    assert "3.11+" in capsys.readouterr().out


def test_start_reexecs_in_venv_when_outside(monkeypatch):
    monkeypatch.setattr(risk, "in_venv", lambda: False)
    monkeypatch.setattr(risk.VENV_PY.__class__, "exists", lambda self: True)
    calls = []
    monkeypatch.setattr(risk.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 7)
    monkeypatch.setattr(risk.sys, "argv", ["risk.py", "start", "--force-new"])
    assert risk.cmd_start(risk.build_parser().parse_args(["start", "--force-new"])) == 7
    assert calls[0][0] == str(risk.VENV_PY) and calls[0][-2:] == ["start", "--force-new"]


def test_start_without_venv_tells_user_to_setup(monkeypatch, capsys):
    monkeypatch.setattr(risk, "in_venv", lambda: False)
    monkeypatch.setattr(risk.VENV_PY.__class__, "exists", lambda self: False)
    assert risk.cmd_start(risk.build_parser().parse_args(["start"])) == 2
    assert "py risk.py setup" in capsys.readouterr().out


def test_start_inside_venv_calls_launcher(monkeypatch):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    launched = Mock(return_value=0)
    monkeypatch.setattr("ui.launch.main", launched)
    assert risk.cmd_start(risk.build_parser().parse_args(["start", "--force-new"])) == 0
    launched.assert_called_once_with(["--force-new"])


def _doctor(monkeypatch, tmp_path, db=None):
    monkeypatch.setattr(risk, "in_venv", lambda: True)
    monkeypatch.setattr(risk, "tcp_open", lambda *a, **k: False)
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
