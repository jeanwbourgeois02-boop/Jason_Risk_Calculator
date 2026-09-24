"""The contract universe: ``config/contracts.csv``, one row per contract root.

Seeded from the research app's ``rvapp/universe/instruments.csv`` (its
``instrument_id`` is our ``root_id``); nothing reads the research folder at run time.

``multiplier`` is the quote-currency amount per 1.0 of the quoted (Bloomberg) price per
contract: contract size, in the quote unit's quantity, times ``price_scale``. The research app's
``price_scale`` turns the raw quoted price into ``quote_unit`` (0.01 for a price quoted in cents,
2 for one quoted per 500 kg). Where the size unit and the quote unit's quantity differ (CME
hogs and cattle are sized in lb and quoted per cwt; SGX rubber is sized in t and quoted per kg)
the size is converted first: CME lean hogs 40,000 lb = 400 cwt, x 1 = 400 USD. The loader
recomputes every row's multiplier and refuses a file whose column disagrees, because a wrong
multiplier is a wrong P&L.
"""

from __future__ import annotations

import csv
import difflib
import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

CONTRACTS_CSV = Path(__file__).resolve().parents[2] / "config" / "contracts.csv"

COLUMNS = (
    "root_id", "name", "sector", "subsector", "exchange", "country", "exchange_code",
    "bbg_root", "bbg_yellow_key", "bbg_verified", "currency", "contract_size", "size_unit",
    "quote_unit", "price_scale", "multiplier", "active_months", "calendar", "delivery",
    "status", "notes",
)
# Added 2026-09-24 (Phases 3-5): read as '' when a file does not carry them.
OPTIONAL_COLUMNS = ("settlement", "option_style", "option_lead_months")

# Exchange-calendar ids (engine/calendars, the exchange-calendars lane) by exchange.
EXCHANGE_CALENDAR = {
    "CME": "US", "CBOT": "US", "NYMEX": "US", "COMEX": "US", "MGEX": "US",
    "ICEUS": "ICE_US",
    "ICE": "ICE_EU",
    "LME": "LME",
    "SHFE": "CN", "INE": "CN", "DCE": "CN", "ZCE": "CN", "GFEX": "CN",
    "OSE": "JP", "TOCOM": "JP",
    "SGX": "SG",
    "EURONEXT": "EURONEXT",
    "BMD": "MY",
    "HKEX": "HK",
    "EEX": "EEX",
    "GME": "AE",
}
CALENDAR_IDS = frozenset(EXCHANGE_CALENDAR.values())
DELIVERY = ("physical", "cash", "")
SETTLEMENTS = ("average", "")
OPTION_STYLES = ("american", "european", "")
STATUSES = ("active", "illiquid", "suspended")

# The research app's quant/units.py factors: kilograms, litres, megajoules per unit.
_MASS_KG = {"t": 1000.0, "st": 907.18474, "lt": 1016.0469088, "cwt": 45.359237, "kg": 1.0,
            "g": 0.001, "lb": 0.45359237, "oz": 0.0311034768}
_VOLUME_L = {"bbl": 158.987294928, "gal": 3.785411784, "l": 1.0, "kl": 1000.0, "m3": 1000.0}
_ENERGY_MJ = {"mmbtu": 1055.05585262, "mwh": 3600.0, "gj": 1000.0, "therm": 105.505585262}

_ROOT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*:[A-Z0-9]+$")


def quantity_factor(from_unit: str, to_unit: str) -> float:
    """How many ``to_unit`` make one ``from_unit``: ``('lb', 'cwt')`` -> 0.01. Same dimension only."""
    if from_unit == to_unit:
        return 1.0
    for table in (_MASS_KG, _VOLUME_L, _ENERGY_MJ):
        if from_unit in table and to_unit in table:
            return table[from_unit] / table[to_unit]
    raise ValueError(f"cannot convert {from_unit!r} to {to_unit!r}")


@dataclass(frozen=True)
class ContractRoot:
    """One contract root (one futures product), a row of ``config/contracts.csv``."""

    root_id: str             # 'NYMEX:CL': EXCHANGE:CODE, never the bare code (codes collide)
    name: str
    sector: str              # energy | metals | agriculture | ferrous | chemicals | freight
    subsector: str
    exchange: str            # 'NYMEX'
    country: str             # ISO alpha-2; 'CN' = a mainland Chinese exchange
    exchange_code: str       # 'CL'
    bbg_root: str            # 'CL'; 'C' for CBOT corn (padded to 'C ' in tickers); 'ZZ...' = placeholder
    bbg_yellow_key: str      # 'Comdty'
    bbg_verified: bool       # True only when the root was checked on a Bloomberg terminal
    currency: str            # quote currency of the price and of local P&L
    contract_size: float     # in size_unit
    size_unit: str
    quote_unit: str          # '<currency>/<unit>' after price_scale
    price_scale: float       # quote_unit price = quoted price x price_scale
    multiplier: float        # currency amount per 1.0 of quoted price per contract
    active_months: Tuple[int, ...]  # main-contract cycle; all 12 when the file leaves it blank
    calendar: str            # exchange-calendar id: US, ICE_US, ICE_EU, LME, CN, JP, SG, ...
    delivery: str            # 'physical' | 'cash' | '' (not known)
    status: str              # active | illiquid | suspended
    notes: str
    # 'average': settles on the average of an index over the contract month (the Platts / Argus /
    # Fastmarkets / CRU swap futures, SGX iron ore, the aluminium premiums, month power);
    # '' otherwise or not known (never guessed; read as an ordinary future)
    settlement: str = ""
    # style of the exchange's options on this root: 'american' | 'european' | '' (not known,
    # read as American by data/contracts/options.py)
    option_style: str = ""
    # how many months before its contract month an option on this root expires (1: the month
    # before, CME's usual; 0: in the contract month, LME-style and averaging contracts; 2: ICE
    # Brent); None when not known. Only the prime-broker dated option form needs it.
    option_lead_months: Optional[int] = None

    @property
    def averaging(self) -> bool:
        """True when the contract settles on an average over its contract month."""
        return self.settlement == "average"

    @property
    def bbg_placeholder(self) -> bool:
        """True when ``bbg_root`` is the research app's placeholder ('ZZ' prefix: not known yet)."""
        return self.bbg_root.startswith("ZZ")


def _bool(text: str, where: str) -> bool:
    t = text.strip().lower()
    if t in ("true", "1", "yes", "y"):
        return True
    if t in ("false", "0", "no", "n", ""):
        return False
    raise ValueError(f"{where}: bbg_verified {text!r} is not true / false")


def _months(text: str, where: str) -> Tuple[int, ...]:
    if not text.strip():
        return tuple(range(1, 13))
    months = tuple(sorted({int(p) for p in text.split(",") if p.strip()}))
    if not months or any(m < 1 or m > 12 for m in months):
        raise ValueError(f"{where}: active_months {text!r} must be months 1-12")
    return months


def _root_from_row(raw: Dict[str, str], line: int) -> ContractRoot:
    where = f"contracts.csv line {line} ({raw.get('root_id', '')})"
    root_id = raw["root_id"].strip()
    if not _ROOT_ID_RE.match(root_id):
        raise ValueError(f"{where}: root_id must be EXCHANGE:CODE")
    exchange, code = root_id.split(":")
    if raw["exchange"].strip() != exchange or raw["exchange_code"].strip() != code:
        raise ValueError(f"{where}: exchange / exchange_code do not repeat the two halves of root_id")
    size = float(raw["contract_size"])
    scale = float(raw["price_scale"] or 1)
    multiplier = float(raw["multiplier"])
    currency, _, quote_qty = raw["quote_unit"].partition("/")
    if currency.strip() != raw["currency"].strip():
        raise ValueError(f"{where}: quote_unit {raw['quote_unit']!r} is not in the row's currency")
    expected = size * quantity_factor(raw["size_unit"].strip(), quote_qty.strip()) * scale
    if size <= 0 or scale <= 0 or abs(multiplier - expected) > 1e-9 * max(1.0, abs(expected)):
        raise ValueError(f"{where}: multiplier {multiplier} is not contract_size x unit factor x "
                         f"price_scale = {expected}")
    calendar = raw["calendar"].strip()
    if calendar not in CALENDAR_IDS or EXCHANGE_CALENDAR.get(exchange) != calendar:
        raise ValueError(f"{where}: calendar {calendar!r} is not the exchange's "
                         f"({EXCHANGE_CALENDAR.get(exchange)!r})")
    delivery = raw["delivery"].strip().lower()
    if delivery not in DELIVERY:
        raise ValueError(f"{where}: delivery {delivery!r} is not physical, cash or blank")
    status = raw["status"].strip()
    if status not in STATUSES:
        raise ValueError(f"{where}: status {status!r} is not one of {STATUSES}")
    settlement = (raw.get("settlement") or "").strip().lower()
    if settlement not in SETTLEMENTS:
        raise ValueError(f"{where}: settlement {settlement!r} is not 'average' or blank")
    option_style = (raw.get("option_style") or "").strip().lower()
    if option_style not in OPTION_STYLES:
        raise ValueError(f"{where}: option_style {option_style!r} is not american, european or blank")
    lead_text = (raw.get("option_lead_months") or "").strip()
    if lead_text and not (lead_text.isdigit() and int(lead_text) <= 3):
        raise ValueError(f"{where}: option_lead_months {lead_text!r} is not 0-3 or blank")
    lead = int(lead_text) if lead_text else None
    return ContractRoot(
        root_id=root_id, name=raw["name"].strip(), sector=raw["sector"].strip(),
        subsector=raw["subsector"].strip(), exchange=exchange, country=raw["country"].strip(),
        exchange_code=code, bbg_root=raw["bbg_root"].strip(),
        bbg_yellow_key=raw["bbg_yellow_key"].strip() or "Comdty",
        bbg_verified=_bool(raw["bbg_verified"], where), currency=raw["currency"].strip(),
        contract_size=size, size_unit=raw["size_unit"].strip(), quote_unit=raw["quote_unit"].strip(),
        price_scale=scale, multiplier=multiplier, active_months=_months(raw["active_months"], where),
        calendar=calendar, delivery=delivery, status=status, notes=raw["notes"].strip(),
        settlement=settlement, option_style=option_style, option_lead_months=lead,
    )


@functools.lru_cache(maxsize=4)
def _load(path: str) -> Tuple[ContractRoot, ...]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"{path}: missing columns {missing}")
        roots = tuple(_root_from_row(raw, n) for n, raw in enumerate(reader, start=2))
    seen: Dict[str, str] = {}
    for root in roots:
        if root.root_id in seen:
            raise ValueError(f"{path}: root_id {root.root_id} appears twice")
        seen[root.root_id] = root.root_id
    tickers: Dict[Tuple[str, str], str] = {}
    for root in roots:
        key = (root.bbg_root, root.bbg_yellow_key)
        if key in tickers:
            raise ValueError(f"{path}: Bloomberg root {root.bbg_root!r} is used by both "
                             f"{tickers[key]} and {root.root_id}")
        tickers[key] = root.root_id
    return roots


def load_roots(path: Optional[Path] = None) -> Dict[str, ContractRoot]:
    """Every contract root, keyed by ``root_id``. Read once per path and cached."""
    return {root.root_id: root for root in _load(str(path or CONTRACTS_CSV))}


def get_root(root_id: str, path: Optional[Path] = None) -> ContractRoot:
    """The root for ``'NYMEX:CL'`` (case and spaces forgiven); KeyError naming close matches."""
    roots = load_roots(path)
    key = re.sub(r"\s+", "", str(root_id or "")).upper()
    if key in roots:
        return roots[key]
    close = difflib.get_close_matches(key, list(roots), n=5, cutoff=0.6)
    code = key.split(":")[-1]
    close += [r for r in roots if r.split(":")[1] == code and r not in close]
    hint = f"; close matches: {', '.join(close)}" if close else ""
    raise KeyError(f"unknown contract root {root_id!r}{hint}")
