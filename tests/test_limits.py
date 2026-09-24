"""margin-limits: the initial margin estimate with spread credits, and the limit checks."""
import sqlite3
from pathlib import Path

import pytest

from data.contracts import get_root
from data.ingest.schema import create_schema
from engine.limits import (
    BASIS, BREACH, DEFAULT_LIMITS_PATH, NA, NOT_SET, OK, WARN, LimitsConfigError, limit_checks, load_limits,
    margin_estimate,
)

AS_OF = "2026-09-15"
TD = "2026-09-01"
CLZ6, CLF7 = "CLZ26 Comdty", "CLF27 Comdty"
EXPIRY = {CLZ6: "2026-11-19", CLF7: "2026-12-17"}
ROOT = {CLZ6: "NYMEX:CL", CLF7: "NYMEX:CL"}

BASE_YAML = """
margin:
  outright_rate:
    sectors: {energy: 0.10, metals: 0.08}
    roots: {}
  usd_per_lot: {}
  spread_credit: {calendar: 0.70, benchmark: 0.50, processing: 0.40, substitution: 0.30}
"""


# ------------------------------------------------------------------ fixtures
def _db():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _future(conn, tid, inst, lots, fill, trade_date=TD, theme=""):
    root = get_root(ROOT[inst])
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (inst, root.root_id, root.currency, root.multiplier, inst, EXPIRY[inst]))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": inst, "product": "FUTURE", "package_id": tid,
            "trade_date": trade_date, "quantity": lots, "price": fill, "account": "ACC", "counterparty": "C",
            "strategy": "", "trader": "JB", "description": "d", "theme": theme}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, lots * root.multiplier * fill, trade_date, EXPIRY[inst], fill))


def _px(conn, inst, value, day=AS_OF):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH','t')", (day, inst, EXPIRY[inst], value))


def _cfg(tmp_path, text=BASE_YAML, name="limits.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _row(result, root, month):
    hits = [r for r in result["rows"] if r["root_id"] == root and r["month"] == month]
    assert len(hits) == 1, result["rows"]
    return hits[0]


def _only(checks, **match):
    hits = [c for c in checks if all(c.get(k) == v for k, v in match.items())]
    assert len(hits) == 1, checks
    return hits[0]


# ------------------------------------------------------------------ margin: outright
def test_outright_margin_at_the_sector_rate(tmp_path):
    conn = _db()
    _future(conn, "T1", CLZ6, 2, 68.0)
    _px(conn, CLZ6, 70.0)
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path))
    assert out["available"] and out["basis"] == BASIS == "estimate (config/limits.yaml), not exchange SPAN"
    row = _row(out, "NYMEX:CL", "2026-12")
    assert row["delta_usd"] == pytest.approx(2 * 1000 * 70.0)
    assert row["rate"] == pytest.approx(0.10) and "energy sector rate" in row["rate_source"]
    assert row["charge_usd"] == pytest.approx(14_000.0) and row["margin_usd"] == pytest.approx(14_000.0)
    for block in (out["by_root"]["NYMEX:CL"], out["by_sector"]["energy"], out["book"]):
        assert block["margin_usd"] == pytest.approx(14_000.0)
        assert block["basis"] == BASIS and block["excluded_count"] == 0 and block["caption"] == ""


def test_outright_margin_at_a_root_override_and_per_lot(tmp_path):
    conn = _db()
    _future(conn, "T1", CLZ6, -2, 68.0)
    _px(conn, CLZ6, 70.0)
    rate = BASE_YAML.replace("roots: {}", "roots: {'NYMEX:CL': 0.05}")
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path, rate))
    assert out["book"]["margin_usd"] == pytest.approx(7_000.0)          # |-140,000| x 5 %
    assert "set for NYMEX:CL" in out["by_root"]["NYMEX:CL"]["rate_source"]
    per_lot = rate.replace("usd_per_lot: {}", "usd_per_lot: {'NYMEX:CL': 6500}")
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path, per_lot, "b.yaml"))
    row = _row(out, "NYMEX:CL", "2026-12")
    assert row["rate_kind"] == "per_lot" and row["charge_usd"] == pytest.approx(13_000.0)   # wins over the rate


# ------------------------------------------------------------------ margin: spreads
def test_calendar_spread_takes_its_credit(tmp_path):
    conn = _db()
    _future(conn, "T1", CLZ6, 2, 68.0)
    _future(conn, "T2", CLF7, -2, 69.0)
    _px(conn, CLZ6, 70.0)
    _px(conn, CLF7, 71.0)
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path))
    (spread,) = out["spreads"]
    assert spread["credit_key"] == "calendar" and spread["credit_pct"] == pytest.approx(0.70)
    gross = 14_000.0 + 14_200.0                                          # months are never netted
    assert spread["charge_on_matched_usd"] == pytest.approx(gross)
    assert spread["credit_usd"] == pytest.approx(0.70 * gross)
    assert spread["leftover_charge_usd"] == pytest.approx(0.0)
    assert out["book"]["gross_charge_usd"] == pytest.approx(gross)
    assert out["book"]["margin_usd"] == pytest.approx(0.30 * gross)


def test_leftover_is_charged_at_the_outright_rate(tmp_path):
    conn = _db()                                                         # a bundle: 3 long against 2 short
    _future(conn, "T1", CLZ6, 3, 68.0, theme="cl roll")
    _future(conn, "T2", CLF7, -2, 69.0, theme="cl roll")
    _px(conn, CLZ6, 70.0)
    _px(conn, CLF7, 71.0)
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path))
    (spread,) = out["spreads"]
    assert spread["credit_key"] == "calendar"
    (left,) = spread["leftover"]
    assert left["lots"] == pytest.approx(1.0) and left["charge_usd"] == pytest.approx(7_000.0)
    matched = 2 * 7_000.0 + 2 * 7_100.0
    assert spread["credit_usd"] == pytest.approx(0.70 * matched)
    assert out["book"]["margin_usd"] == pytest.approx(7_000.0 + 0.30 * matched)


def test_lots_offset_elsewhere_in_the_month_are_not_credited_twice(tmp_path):
    conn = _db()
    _future(conn, "T1", CLZ6, 2, 68.0)
    _future(conn, "T2", CLF7, -2, 69.0)
    _future(conn, "T3", CLZ6, -1, 69.5, trade_date="2026-09-08")       # a later outright sale
    _px(conn, CLZ6, 70.0)
    _px(conn, CLF7, 71.0)
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path))
    z = _row(out, "NYMEX:CL", "2026-12")
    assert z["charge_usd"] == pytest.approx(7_000.0)                     # net 1 lot in December
    assert z["spread_credit_usd"] == pytest.approx(0.70 * 7_000.0)       # only that one lot credited
    assert "offset elsewhere" in out["spreads"][0]["note"]


# ------------------------------------------------------------------ margin: n/a
def test_position_without_a_usd_figure_is_na_and_excluded(tmp_path):
    conn = _db()
    _future(conn, "T1", CLZ6, 2, 68.0)
    _future(conn, "T2", CLF7, 1, 69.0, trade_date="2026-09-02")
    _px(conn, CLZ6, 70.0)                                                # no price for January
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path))
    jan = _row(out, "NYMEX:CL", "2027-01")
    assert jan["charge_usd"] is None and jan["margin_usd"] is None and "FUTURE_PX" in jan["reason"]
    book = out["book"]
    assert book["margin_usd"] == pytest.approx(14_000.0)
    assert book["excluded_count"] == 1 and book["excluded"] == ["NYMEX:CL 2027-01"]
    assert book["caption"].startswith("excludes 1 of 2")
    assert any("2027-01" in r for r in out["reasons"])


def test_no_rate_means_na_never_a_made_up_rate(tmp_path):
    conn = _db()
    _future(conn, "T1", CLZ6, 2, 68.0)
    _px(conn, CLZ6, 70.0)
    text = BASE_YAML.replace("energy: 0.10", "energy: null")
    out = margin_estimate(conn, AS_OF, config_path=_cfg(tmp_path, text))
    row = _row(out, "NYMEX:CL", "2026-12")
    assert row["charge_usd"] is None and "no margin rate for NYMEX:CL" in row["reason"]
    assert out["book"]["excluded_count"] == 1 and out["book"]["margin_usd"] == 0.0
    missing = margin_estimate(conn, AS_OF, config_path=tmp_path / "absent.yaml")
    assert _row(missing, "NYMEX:CL", "2026-12")["charge_usd"] is None
    assert any("not found" in r for r in missing["reasons"])


# ------------------------------------------------------------------ limits
def _book():
    conn = _db()
    _future(conn, "T1", CLZ6, 2, 68.0)
    _future(conn, "T2", CLF7, -3, 69.0, trade_date="2026-09-02")
    _px(conn, CLZ6, 70.0)
    _px(conn, CLF7, 71.0)
    return conn


def test_every_limit_is_not_set_by_default():
    checks = limit_checks(_book(), AS_OF)                                # the repo's config/limits.yaml
    assert checks and {c["level"] for c in checks} == {NOT_SET}
    names = {c["limit"] for c in checks}
    assert names == {"gross_lots", "gross_usd", "net_usd_sector", "net_usd_commodity", "lots_per_contract_month",
                     "exchange_spot_month", "exchange_single_month", "exchange_all_months"}
    assert _only(checks, limit="gross_lots")["value"] == pytest.approx(5.0)   # still measured
    assert all("no limit set" in c["reason"] for c in checks)


@pytest.mark.parametrize("limit,level,pct", [(10, OK, 50.0), (6, WARN, 5 / 6 * 100), (4, BREACH, 125.0)])
def test_a_set_limit_at_ok_warn_breach(tmp_path, limit, level, pct):
    text = BASE_YAML + f"warn_fraction: 0.8\ndesk_limits:\n  gross_lots: {limit}\n"
    check = _only(limit_checks(_book(), AS_OF, config_path=_cfg(tmp_path, text)), limit="gross_lots")
    assert check["value"] == pytest.approx(5.0) and check["limit_value"] == limit
    assert check["level"] == level and check["used_pct"] == pytest.approx(pct)


def test_set_limit_with_no_figure_is_na(tmp_path):
    conn = _db()
    _future(conn, "T1", CLZ6, 2, 68.0)                                   # no price: no USD delta
    text = BASE_YAML + "desk_limits:\n  gross_usd: 1000000\n"
    check = _only(limit_checks(conn, AS_OF, config_path=_cfg(tmp_path, text)), limit="gross_usd")
    assert check["level"] == NA and "FUTURE_PX" in check["reason"]


def test_spot_month_single_month_and_all_months(tmp_path):
    text = BASE_YAML + ("exchange_limits:\n"
                        "  'NYMEX:CL': {spot_month: 2, single_month: 10, all_months: 2}\n")
    checks = limit_checks(_book(), AS_OF, config_path=_cfg(tmp_path, text))
    spot = _only(checks, limit="exchange_spot_month")
    assert spot["scope"].startswith("NYMEX:CL 2026-12 (last trade 2026-11-19")   # the nearest held expiry
    assert spot["value"] == pytest.approx(2.0) and spot["level"] == WARN          # 100 %: at, not over
    single = _only(checks, limit="exchange_single_month")
    assert single["scope"] == "NYMEX:CL 2027-01" and single["value"] == pytest.approx(-3.0)
    assert single["level"] == OK
    every = _only(checks, limit="exchange_all_months")
    assert every["value"] == pytest.approx(-1.0) and every["level"] == OK and every["used_pct"] == pytest.approx(50.0)
    month = _only(checks, limit="lots_per_contract_month", scope="NYMEX:CL 2027-01")
    assert month["value"] == pytest.approx(-3.0) and month["level"] == NOT_SET


# ------------------------------------------------------------------ the YAML
def test_repo_yaml_loads_with_placeholders_and_no_limits():
    cfg = load_limits(DEFAULT_LIMITS_PATH)
    assert cfg.loaded
    assert all(v is None or 0 <= v <= 1 for v in cfg.sector_rates.values())
    assert set(cfg.spread_credit) == {"calendar", "benchmark", "processing", "substitution"}
    assert cfg.gross_lots is None and cfg.gross_usd is None and cfg.exchange == {}
    assert cfg.net_usd_commodity_default is None and cfg.lots_per_month_default is None
    text = DEFAULT_LIMITS_PATH.read_text(encoding="utf-8")
    assert text.count("# placeholder, not the exchange's") >= 9
    assert "CME Rulebook Chapter 5" in text


@pytest.mark.parametrize("bad,words", [
    (BASE_YAML.replace("energy: 0.10", "energy: 1.5"), "out of range"),
    (BASE_YAML.replace("energy: 0.10", "energy: -0.1"), "out of range"),
    (BASE_YAML.replace("calendar: 0.70", "calendar: 2"), "out of range"),
    (BASE_YAML.replace("energy: 0.10", "energie: 0.10"), "'energie' is not a sector"),
    (BASE_YAML.replace("roots: {}", "roots: {'NYMEX:CLL': 0.1}"), "'NYMEX:CLL' is not a contract root"),
    (BASE_YAML.replace("calendar: 0.70", "calender: 0.70"), "unknown key"),
    (BASE_YAML + "exchange_limits:\n  'NYMEX:QQ': {spot_month: 1}\n", "not a contract root"),
    (BASE_YAML + "desk_limits:\n  gross_lot: 10\n", "unknown key"),
    (BASE_YAML + "desk_limits:\n  gross_lots: -1\n", "out of range"),
    (BASE_YAML + "warn_fraction: 0\n", "out of range"),
])
def test_yaml_refuses_bad_values_and_misspellings(tmp_path, bad, words):
    with pytest.raises(LimitsConfigError, match=words):
        load_limits(_cfg(tmp_path, bad))


def test_a_refused_file_blanks_the_figures_with_the_reason(tmp_path):
    bad = _cfg(tmp_path, BASE_YAML.replace("energy: 0.10", "energie: 0.10"))
    conn = _book()
    out = margin_estimate(conn, AS_OF, config_path=bad)
    assert not out["available"] and "energie" in out["reasons"][0]
    (row,) = limit_checks(conn, AS_OF, config_path=bad)
    assert row["limit"] == "config" and row["level"] == NA and "energie" in row["reason"]


def test_empty_book(tmp_path):
    out = margin_estimate(_db(), AS_OF, config_path=_cfg(tmp_path))
    assert out["rows"] == [] and out["book"]["margin_usd"] == 0.0
    assert isinstance(limit_checks(_db(), AS_OF, config_path=Path(_cfg(tmp_path))), list)
