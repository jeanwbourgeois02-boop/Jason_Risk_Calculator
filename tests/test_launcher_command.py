"""The `chelsea` PowerShell command that `2_launcher.py setup` installs (user, 2026-09-24).

This project was forked from Henry's risk monitor, whose setup writes its own `pnl`
function into the same PowerShell profiles. These tests pin that installing `chelsea`
leaves every other byte of a profile, Henry's `pnl` blocks included, exactly as it was."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_launcher():
    spec = importlib.util.spec_from_file_location("launcher_command", ROOT / "2_launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


launcher = _load_launcher()

# Henry's profile as it is on the user's PC (2026-09-24): UTF-8 with BOM, CRLF, two `pnl`
# functions, the second inside the risk-monitor markers.
HENRY_PROFILE = (
    "\ufeff# risk-monitor: type `pnl` in any PowerShell terminal to launch the app from the single\r\n"
    "# working copy on the latest code. Managed by the risk-monitor project; edit there.\r\n"
    "function pnl {\r\n"
    "    Set-Location 'C:\\Users\\jeanw\\risk-monitor'\r\n"
    "    py -3 risk.py start\r\n"
    "}\r\n"
    "# --- risk-monitor pnl function (installed by `setup`) ---\r\n"
    "function pnl {\r\n"
    "    Set-Location 'C:\\Users\\jeanw\\risk-monitor'\r\n"
    "    py -3 2_launcher.py start\r\n"
    "}\r\n"
    "# --- end risk-monitor pnl function ---\r\n"
).encode("utf-8")


@pytest.fixture
def profile(tmp_path, monkeypatch):
    path = tmp_path / "WindowsPowerShell" / "profile.ps1"
    monkeypatch.setattr(launcher, "_profile_paths", lambda: [path])
    return path


def test_block_defines_chelsea_in_this_clone_and_nothing_of_henrys():
    block = launcher.pnl_function_block()
    assert "function chelsea {" in block
    assert f"Set-Location '{launcher.ROOT}'" in block
    assert "py -3 2_launcher.py start" in block
    assert "function pnl" not in block
    assert "risk-monitor" not in block
    assert block.startswith(launcher.CHELSEA_MARKER) and block.rstrip().endswith(launcher.CHELSEA_END_MARKER)


def test_henrys_profile_is_kept_byte_for_byte_and_chelsea_appended_once(profile):
    profile.parent.mkdir(parents=True)
    profile.write_bytes(HENRY_PROFILE)
    assert launcher.install_pnl_function() == [str(profile)]
    after = profile.read_bytes()
    assert after.startswith(HENRY_PROFILE)
    added = after[len(HENRY_PROFILE):]
    assert added == launcher.pnl_function_block("\r\n").encode("utf-8")
    assert added.count(b"\r\n") == added.count(b"\n")  # CRLF like the rest of the file
    assert after.count(b"function chelsea") == 1


def test_second_install_changes_nothing(profile):
    profile.parent.mkdir(parents=True)
    profile.write_bytes(HENRY_PROFILE)
    launcher.install_pnl_function()
    once = profile.read_bytes()
    launcher.install_pnl_function()
    assert profile.read_bytes() == once


def test_own_block_in_the_middle_is_replaced_in_place(profile):
    before = b"# before\r\nSet-Alias ll Get-ChildItem\r\n"
    after = b"# after\r\n" + HENRY_PROFILE.removeprefix(b"\xef\xbb\xbf")
    stale = (launcher.CHELSEA_MARKER + "\r\nfunction chelsea {\r\n    Set-Location 'C:\\old'\r\n}\r\n"
             + launcher.CHELSEA_END_MARKER + "\r\n").encode("utf-8")
    profile.parent.mkdir(parents=True)
    profile.write_bytes(before + stale + after)
    launcher.install_pnl_function()
    assert profile.read_bytes() == before + launcher.pnl_function_block("\r\n").encode("utf-8") + after


def test_missing_profile_is_created_with_only_chelsea(profile):
    launcher.install_pnl_function()
    text = profile.read_bytes().decode("utf-8")
    newline = "\r\n" if launcher.os.name == "nt" else "\n"
    assert text == launcher.pnl_function_block(newline)
