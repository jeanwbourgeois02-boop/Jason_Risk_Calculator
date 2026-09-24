"""engine/risk/history.py without a parquet engine: the file list, the status shape, the
folder search and the reasons given when there is nothing to read. The parquet reads
themselves are exercised by tests/test_risk.py (risk-metrics'), which needs pyarrow."""
from __future__ import annotations

import json

import pytest

from engine.risk import history as history_mod
from engine.risk.history import FILES, SPOT_FILE, YIELDS_FILE, History, load_history

STATUS_KEYS = {"available", "path", "last_date", "reason", "note", "candidates", "files"}
FILE_STATUS_KEYS = {"file", "path", "loaded", "rows", "columns", "first_date", "last_date", "reason"}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(history_mod.ENV_VAR, raising=False)
    history_mod._CACHE.clear()
    history_mod._LAST_DATE_MEMO.clear()


def test_the_files_read_are_spot_and_yields_only():
    assert FILES == {"spot": "bbg_raw_fx_marks.parquet", "yields": "bbg_raw_fx_yields.parquet"}
    assert not hasattr(history_mod, "RATES_FILE")                 # the par swap rate file is retired (Phase 2)
    assert "swap_rates" not in History.__dataclass_fields__


def test_a_missing_folder_is_not_available_and_names_the_path(tmp_path):
    absent = tmp_path / "nowhere"
    h = load_history(absent)
    assert not h.available and h.reason == f"no market history folder: tried {absent}"
    status = h.status()
    assert set(status) == STATUS_KEYS and status["files"] == {} and status["last_date"] is None
    assert status["candidates"] == [{"path": str(absent), "exists": False, "last_date": None}]
    json.dumps(status)                                             # JSON-friendly


def test_no_default_folder_names_every_path_tried(tmp_path, monkeypatch):
    dirs = (tmp_path / "a", tmp_path / "b", tmp_path / "c")
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", dirs)
    h = load_history()
    assert not h.available
    assert h.reason == "no market history folder: tried " + ", ".join(str(d) for d in dirs)
    assert [c["path"] for c in h.status()["candidates"]] == [str(d) for d in dirs]


def test_the_environment_variable_is_the_only_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (tmp_path / "default",))
    (tmp_path / "default").mkdir()
    monkeypatch.setenv(history_mod.ENV_VAR, str(tmp_path / "env"))
    h = load_history()
    assert not h.available and h.reason == f"no market history folder: tried {tmp_path / 'env'}"


def test_a_folder_without_files_names_each_missing_file(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    h = load_history(folder)
    assert not h.available and h.reason == f"no spot history: {folder / SPOT_FILE} not found"
    status = h.status()
    assert set(status) == STATUS_KEYS and set(status["files"]) == {"spot", "yields"}
    for name, filename in FILES.items():
        f = status["files"][name]
        assert set(f) == FILE_STATUS_KEYS
        assert (f["file"], f["loaded"], f["rows"], f["columns"]) == (filename, False, 0, [])
        assert f["reason"] == f"{folder / filename} not found"
    assert status["files"]["yields"]["path"] == str(folder / YIELDS_FILE)
    json.dumps(status)


def test_an_unreadable_spot_file_is_a_reason_never_an_exception(tmp_path):
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / SPOT_FILE).write_bytes(b"not a parquet file")
    h = load_history(folder)
    assert not h.available and h.reason.startswith(f"no spot history: {folder / SPOT_FILE} could not be read (")
    assert history_mod.spot_last_date(folder) is None
    assert history_mod.spot_last_date(tmp_path / "absent") is None


def test_the_cache_key_covers_the_read_files_alone(tmp_path):
    folder = tmp_path / "k"
    folder.mkdir()
    (folder / "bbg_raw_rates.parquet").write_bytes(b"x")          # a retired file on disk is ignored
    key = history_mod._cache_key(folder)
    assert key == (str(folder), ((SPOT_FILE, None), (YIELDS_FILE, None)))
    assert load_history(folder) is load_history(folder)           # cached while nothing changes


def test_default_folders_with_no_spot_file_keep_their_order(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (a, b))
    folder, seen, note = history_mod.history_dir()
    assert folder == a and [c["exists"] for c in seen] == [True, True]
    assert note == f"2 copies found, using {a} (no spot file); {b} has no spot file"
