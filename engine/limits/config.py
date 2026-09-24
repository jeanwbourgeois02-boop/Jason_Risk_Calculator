"""``config/limits.yaml``: the margin rates, spread credits and limits, all the user's to set.

``load_limits(path=None)`` reads and checks the file and returns a ``LimitsConfig``. It refuses
(``LimitsConfigError``) a rate or credit outside 0..1, a negative limit or USD-per-lot figure, a
``warn_fraction`` outside (0, 1], a sector or contract root ``config/contracts.csv`` does not know,
and any key the file does not document, so a misspelling is never silently ignored. A missing
file is not an error: every rate and limit is then unset (``loaded`` False, ``note`` says so).

Nothing here has a default number: an unset rate is None and the commodity is n/a; an unset
limit is None and the check is NOT_SET.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

import yaml

from data.contracts import load_roots

DEFAULT_LIMITS_PATH = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
CREDIT_KEYS = ("calendar", "benchmark", "processing", "substitution")
EXCHANGE_KEYS = ("spot_month", "single_month", "all_months")
DEFAULT_WARN_FRACTION = 0.8


class LimitsConfigError(ValueError):
    """``config/limits.yaml`` could not be read or does not validate."""


@dataclass(frozen=True)
class LimitsConfig:
    sector_rates: Dict[str, Optional[float]] = field(default_factory=dict)
    root_rates: Dict[str, Optional[float]] = field(default_factory=dict)
    usd_per_lot: Dict[str, Optional[float]] = field(default_factory=dict)
    spread_credit: Dict[str, Optional[float]] = field(default_factory=dict)
    warn_fraction: float = DEFAULT_WARN_FRACTION
    gross_lots: Optional[float] = None
    gross_usd: Optional[float] = None
    net_usd_commodity_default: Optional[float] = None
    net_usd_commodity: Dict[str, Optional[float]] = field(default_factory=dict)
    net_usd_sector_default: Optional[float] = None
    net_usd_sector: Dict[str, Optional[float]] = field(default_factory=dict)
    lots_per_month_default: Optional[float] = None
    lots_per_month: Dict[str, Optional[float]] = field(default_factory=dict)
    exchange: Dict[str, Dict[str, Optional[float]]] = field(default_factory=dict)
    file: str = ""
    loaded: bool = False
    note: str = ""

    # ---- lookups
    def margin_rate(self, root_id: str, sector: str):
        """(kind, value, source sentence): kind 'per_lot' | 'rate' | None (no figure set)."""
        if self.usd_per_lot.get(root_id) is not None:
            return "per_lot", self.usd_per_lot[root_id], f"USD {self.usd_per_lot[root_id]:,.0f} per lot for {root_id}"
        if self.root_rates.get(root_id) is not None:
            return "rate", self.root_rates[root_id], f"{self.root_rates[root_id]:.2%} of USD delta, set for {root_id}"
        if self.sector_rates.get(sector) is not None:
            return "rate", self.sector_rates[sector], f"{self.sector_rates[sector]:.2%} of USD delta, the {sector} sector rate"
        return None, None, (f"no margin rate for {root_id} or its sector {sector or '(none)'} in "
                            f"{Path(self.file).name or 'config/limits.yaml'}")

    # A per-root or per-sector figure wins; one left null falls back to the default.
    def net_usd_limit_commodity(self, root_id: str) -> Optional[float]:
        own = self.net_usd_commodity.get(root_id)
        return own if own is not None else self.net_usd_commodity_default

    def net_usd_limit_sector(self, sector: str) -> Optional[float]:
        own = self.net_usd_sector.get(sector)
        return own if own is not None else self.net_usd_sector_default

    def lots_per_month_limit(self, root_id: str) -> Optional[float]:
        own = self.lots_per_month.get(root_id)
        return own if own is not None else self.lots_per_month_default

    def exchange_limit(self, root_id: str, key: str) -> Optional[float]:
        return (self.exchange.get(root_id) or {}).get(key)


# ------------------------------------------------------------------ checks
def _where(*parts: str) -> str:
    return ".".join(parts)


def _keys(section: Any, allowed: Iterable[str], where: str) -> dict:
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise LimitsConfigError(f"{where} must be a mapping, not {type(section).__name__}")
    unknown = [str(k) for k in section if str(k) not in allowed]
    if unknown:
        raise LimitsConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))} "
                                f"(expected {', '.join(allowed)})")
    return {str(k): v for k, v in section.items()}


def _number(value: Any, where: str, low: float = 0.0, high: Optional[float] = None,
            low_open: bool = False) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LimitsConfigError(f"{where}: {value!r} is not a number (null leaves it unset)")
    x = float(value)
    if not math.isfinite(x) or x < low or (low_open and x == low) or (high is not None and x > high):
        span = f"{'(' if low_open else '['}{low:g}, {high:g}]" if high is not None else f">= {low:g}"
        raise LimitsConfigError(f"{where}: {value!r} is out of range ({span})")
    return x


def _named(section: Any, known: Iterable[str], what: str, where: str, **bounds) -> Dict[str, Optional[float]]:
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise LimitsConfigError(f"{where} must be a mapping of {what} to a number")
    known = set(known)
    out = {}
    for k, v in section.items():
        key = str(k).strip()
        if key not in known:
            raise LimitsConfigError(f"{where}: {key!r} is not a {what} of config/contracts.csv")
        out[key] = _number(v, _where(where, key), **bounds)
    return out


def load_limits(path: Optional[Union[str, Path]] = None, roots: Optional[dict] = None) -> LimitsConfig:
    """``config/limits.yaml`` read and checked (see the module docstring)."""
    path = Path(path) if path is not None else DEFAULT_LIMITS_PATH
    if not path.exists():
        return LimitsConfig(file=str(path), loaded=False,
                            note=f"{path.name} not found: no margin rate and no limit is set")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise LimitsConfigError(f"{path.name} could not be read: {exc}") from exc
    roots = roots if roots is not None else load_roots()
    root_ids = set(roots)
    sectors = {r.sector for r in roots.values()}

    top = _keys(raw, ("margin", "warn_fraction", "desk_limits", "exchange_limits"), path.name)
    margin = _keys(top.get("margin"), ("outright_rate", "usd_per_lot", "spread_credit"), "margin")
    outright = _keys(margin.get("outright_rate"), ("sectors", "roots"), "margin.outright_rate")
    rate = {"low": 0.0, "high": 1.0}
    sector_rates = _named(outright.get("sectors"), sectors, "sector", "margin.outright_rate.sectors", **rate)
    root_rates = _named(outright.get("roots"), root_ids, "contract root", "margin.outright_rate.roots", **rate)
    per_lot = _named(margin.get("usd_per_lot"), root_ids, "contract root", "margin.usd_per_lot")
    credit_raw = _keys(margin.get("spread_credit"), CREDIT_KEYS, "margin.spread_credit")
    credit = {k: _number(v, f"margin.spread_credit.{k}", **rate) for k, v in credit_raw.items()}

    warn = _number(top.get("warn_fraction", DEFAULT_WARN_FRACTION), "warn_fraction", 0.0, 1.0, low_open=True)
    warn = DEFAULT_WARN_FRACTION if warn is None else warn

    desk = _keys(top.get("desk_limits"), ("gross_lots", "gross_usd", "net_usd_per_commodity",
                                          "net_usd_per_sector", "lots_per_contract_month"), "desk_limits")
    com = _keys(desk.get("net_usd_per_commodity"), ("default", "roots"), "desk_limits.net_usd_per_commodity")
    sec = _keys(desk.get("net_usd_per_sector"), ("default", "sectors"), "desk_limits.net_usd_per_sector")
    lpm = _keys(desk.get("lots_per_contract_month"), ("default", "roots"), "desk_limits.lots_per_contract_month")

    exchange_raw = top.get("exchange_limits")
    if exchange_raw is not None and not isinstance(exchange_raw, dict):
        raise LimitsConfigError("exchange_limits must be a mapping of contract root to its limits")
    exchange: Dict[str, Dict[str, Optional[float]]] = {}
    for k, v in (exchange_raw or {}).items():
        rid = str(k).strip()
        if rid not in root_ids:
            raise LimitsConfigError(f"exchange_limits: {rid!r} is not a contract root of config/contracts.csv")
        entry = _keys(v, EXCHANGE_KEYS, f"exchange_limits.{rid}")
        exchange[rid] = {key: _number(entry.get(key), f"exchange_limits.{rid}.{key}") for key in EXCHANGE_KEYS}

    return LimitsConfig(
        sector_rates=sector_rates, root_rates=root_rates, usd_per_lot=per_lot, spread_credit=credit,
        warn_fraction=warn,
        gross_lots=_number(desk.get("gross_lots"), "desk_limits.gross_lots"),
        gross_usd=_number(desk.get("gross_usd"), "desk_limits.gross_usd"),
        net_usd_commodity_default=_number(com.get("default"), "desk_limits.net_usd_per_commodity.default"),
        net_usd_commodity=_named(com.get("roots"), root_ids, "contract root", "desk_limits.net_usd_per_commodity.roots"),
        net_usd_sector_default=_number(sec.get("default"), "desk_limits.net_usd_per_sector.default"),
        net_usd_sector=_named(sec.get("sectors"), sectors, "sector", "desk_limits.net_usd_per_sector.sectors"),
        lots_per_month_default=_number(lpm.get("default"), "desk_limits.lots_per_contract_month.default"),
        lots_per_month=_named(lpm.get("roots"), root_ids, "contract root", "desk_limits.lots_per_contract_month.roots"),
        exchange=exchange, file=str(path), loaded=True, note="",
    )
