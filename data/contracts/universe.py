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
import datetime as dt
import difflib
import functools
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Dict, Optional, Tuple

CONTRACTS_CSV = Path(__file__).resolve().parents[2] / "config" / "contracts.csv"

COLUMNS = (
    "root_id", "name", "sector", "subsector", "exchange", "country", "exchange_code",
    "bbg_root", "bbg_yellow_key", "bbg_verified", "currency", "contract_size", "size_unit",
    "quote_unit", "price_scale", "multiplier", "active_months", "calendar", "delivery",
    "status", "notes",
)
# Added 2026-09-24 (Phases 3-5), 2026-09-28 (broker_price_scale) and 2026-09-29 (family,
# close_time): read as '' when a file does not carry them.
OPTIONAL_COLUMNS = ("settlement", "option_style", "option_lead_months", "broker_price_scale",
                    "family", "close_time")

# The commodity family a root belongs to: how the desk groups whole trades (the P&L and Risk
# tabs' "Commodity family"), coarser than the subsector where the desk reads several as one
# (iron ore, HRC, rebar and coking coal are all "ferrous"; gasoline and gasoil "products";
# soybeans, meal, palm and rapeseed "oilseeds"). Display grouping only: never a P&L or risk input.
# A ``family`` outside this list is refused, so a typo never opens a family of its own.
FAMILIES = (
    # metals
    "copper", "aluminium", "zinc", "lead", "tin", "nickel", "gold", "silver", "platinum",
    "palladium", "lithium", "cobalt", "silicon",
    # ferrous
    "ferrous",
    # energy
    "crude", "products", "lpg", "gas", "coal", "carbon", "power", "freight",
    # agriculture
    "grains", "oilseeds", "softs", "cattle", "hogs", "eggs", "fruit", "rubber", "forest products",
    # chemicals
    "polyester", "plastics", "olefins", "aromatics", "methanol", "fertilisers", "glass",
    "caustic soda",
    # currency futures (the SGX USD/CNH future, an FX hedge)
    "fx",
)

# Each exchange's daily close, local time and IANA time zone: the time of the settlement that
# Bloomberg's daily PX_LAST reflects (the app's futures close, "Mark time" in CLAUDE.md). The
# day session on the Chinese exchanges (the night session belongs to the next trading day), the
# closing prices on the LME, the settlement window on the US and European exchanges. A root whose
# own settlement differs (COMEX copper 13:00, ICE Brent 19:30) carries it in ``close_time``.
# Approximate, not verified on a terminal: for the "legs closed ~Nh apart" note and for choosing
# 2-day moves where the legs close hours apart, never a mark time and never in P&L.
EXCHANGE_CLOSE = {
    "SHFE": ("15:00", "Asia/Shanghai"), "INE": ("15:00", "Asia/Shanghai"),
    "DCE": ("15:00", "Asia/Shanghai"), "ZCE": ("15:00", "Asia/Shanghai"),
    "GFEX": ("15:00", "Asia/Shanghai"),
    "HKEX": ("16:30", "Asia/Hong_Kong"),
    "SGX": ("19:00", "Asia/Singapore"),
    "OSE": ("15:15", "Asia/Tokyo"), "TOCOM": ("15:15", "Asia/Tokyo"),
    "BMD": ("18:00", "Asia/Kuala_Lumpur"),
    "GME": ("12:30", "Asia/Dubai"),           # Oman crude settles at the Singapore close, 16:30 SGT
    "LME": ("17:00", "Europe/London"),
    "ICE": ("17:30", "Europe/London"),        # ICE Futures Europe; Brent's own 19:30 on its row
    "EEX": ("18:00", "Europe/Berlin"),
    "EURONEXT": ("18:30", "Europe/Paris"),
    "COMEX": ("13:30", "America/New_York"),   # gold; copper and silver on their rows
    "NYMEX": ("14:30", "America/New_York"),
    "ICEUS": ("13:30", "America/New_York"),
    "CME": ("13:00", "America/Chicago"),      # livestock settlement, 12:59-13:00 CT
    "CBOT": ("13:15", "America/Chicago"),
    "MGEX": ("13:15", "America/Chicago"),
}
_CLOSE_RE = re.compile(r"^(?P<hh>[01]\d|2[0-3]):(?P<mm>[0-5]\d)\s+(?P<tz>[A-Za-z_]+(?:/[A-Za-z_+\-0-9]+)+)$")

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
    sector: str              # energy | metals | agriculture | ferrous | chemicals | freight | fx
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
    # the broker's fill x broker_price_scale = the price as Bloomberg quotes it: 100 where the
    # exchange quotes in cents (per lb, per bushel, per gallon) and the prime broker books the
    # fill in whole currency per unit (live cattle 2.19 for Bloomberg's 219; seen in Jason's
    # 2026-09-28 export for feeder cattle, live cattle and COMEX copper); 1 (blank in the file)
    # where the broker's fill is the quoted price
    broker_price_scale: float = 1.0
    # the commodity family ('copper', 'ferrous', 'cattle'; one of FAMILIES): how whole trades
    # are grouped; the subsector when the file carries no family column
    family: str = ""
    # the root's own daily close when it differs from its exchange's ('13:00 America/New_York'
    # for COMEX copper); '' = the exchange's, EXCHANGE_CLOSE. Read it through ``close``.
    close_time: str = ""

    @property
    def close(self) -> Tuple[dt.time, str]:
        """(local time, IANA time zone) of this root's daily close: its own ``close_time`` when
        set, else its exchange's (``exchange_close``). Approximate; never a mark time."""
        if self.close_time:
            return _parse_close(self.close_time, self.root_id)
        return exchange_close(self.exchange)

    @property
    def averaging(self) -> bool:
        """True when the contract settles on an average over its contract month."""
        return self.settlement == "average"

    @property
    def bbg_placeholder(self) -> bool:
        """True when ``bbg_root`` is the research app's placeholder ('ZZ' prefix: not known yet)."""
        return self.bbg_root.startswith("ZZ")


def _parse_close(text: str, where: str) -> Tuple[dt.time, str]:
    """'13:00 America/New_York' -> (time(13, 0), 'America/New_York'); ValueError otherwise."""
    m = _CLOSE_RE.match(str(text or "").strip())
    if not m:
        raise ValueError(f"{where}: close_time {text!r} is not 'HH:MM Area/City'")
    tz = m.group("tz")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"{where}: close_time {text!r}: {tz!r} is not a known time zone") from None
    return dt.time(int(m.group("hh")), int(m.group("mm"))), tz


def exchange_close(exchange: str) -> Tuple[dt.time, str]:
    """(local time, IANA time zone) of an exchange's daily close, the settlement Bloomberg's
    PX_LAST reflects: ``exchange_close('SHFE')`` -> (time(15, 0), 'Asia/Shanghai'). KeyError
    naming the known exchanges for any other. A root with its own close: ``ContractRoot.close``."""
    key = str(exchange or "").strip().upper()
    if key not in EXCHANGE_CLOSE:
        raise KeyError(f"no close time for exchange {exchange!r}; known: {', '.join(sorted(EXCHANGE_CLOSE))}")
    hhmm, tz = EXCHANGE_CLOSE[key]
    return _parse_close(f"{hhmm} {tz}", key)


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
    broker_text = (raw.get("broker_price_scale") or "").strip()
    try:
        broker_scale = float(broker_text) if broker_text else 1.0
    except ValueError:
        raise ValueError(f"{where}: broker_price_scale {broker_text!r} is not a number") from None
    if not broker_scale > 0 or broker_scale == float("inf"):
        raise ValueError(f"{where}: broker_price_scale {broker_text!r} is not a positive number")
    if "family" in raw:
        family = (raw.get("family") or "").strip().lower()
        if family not in FAMILIES:
            raise ValueError(f"{where}: family {raw.get('family')!r} is not one of {', '.join(FAMILIES)}")
    else:
        family = raw["subsector"].strip().lower()     # a file with no family column
    close_time = re.sub(r"\s+", " ", (raw.get("close_time") or "").strip())
    if close_time:
        _parse_close(close_time, where)
    if exchange not in EXCHANGE_CLOSE:
        raise ValueError(f"{where}: exchange {exchange!r} has no close time in EXCHANGE_CLOSE")
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
        broker_price_scale=broker_scale, family=family, close_time=close_time,
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
