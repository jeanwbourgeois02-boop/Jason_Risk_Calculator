"""LME metals as forward instruments (lme-forwards lane).

An LME forward's instrument is its contract root itself ('LME:CA'), the way an FX forward's
is its pair: one instrument, many prompt dates, marked by FWD_OUTRIGHT at each prompt date
and by SPOT = the cash price. The roots come from contract-master
(`data.contracts.load_roots`, `config/contracts.csv`): the LME rows of sector 'metals' (the
base metals, the alloys and cobalt). The LME's ferrous contracts (steel scrap, rebar, HRC)
are monthly cash-settled futures without the prompt structure, so they are not LME forwards.
"""
from __future__ import annotations

import re

from data.contracts import ContractRoot, load_roots

__all__ = ["lme_roots", "lme_root", "lot_tonnes", "is_lme_instrument", "metal_root"]

# Metal names to LME contract codes. The code is also accepted as it is ('CA'), and the root
# id ('LME:CA'). NASAAC and aluminium alloy are two contracts, so neither answers to a bare
# "alloy".
_NAMES = {
    "copper": "CA",
    "aluminium": "AH", "aluminum": "AH", "primary aluminium": "AH", "primary aluminum": "AH",
    "zinc": "ZS",
    "lead": "PB",
    "nickel": "NI",
    "tin": "SN",
    "nasaac": "NA",
    "aluminium alloy": "AA", "aluminum alloy": "AA",
    "cobalt": "CO",
}


def lme_roots() -> dict[str, ContractRoot]:
    """The LME roots that trade as prompt-date forwards, keyed by root id."""
    return {rid: r for rid, r in load_roots().items() if r.exchange == "LME" and r.sector == "metals"}


def _known() -> str:
    first_name = {}
    for name, code in _NAMES.items():
        first_name.setdefault(code, name)
    return ", ".join(f"{rid} {first_name.get(rid[4:], r.subsector)}" for rid, r in sorted(lme_roots().items()))


def lme_root(metal: str) -> str:
    """The root id of an LME forward metal: 'copper', 'Copper', 'CA' or 'LME:CA' -> 'LME:CA'.
    An unknown metal, or an LME contract that is not a prompt-date forward, raises ValueError
    naming the ones known."""
    roots = lme_roots()
    key = re.sub(r"[\s_]+", " ", str(metal or "")).strip().lower()
    code = _NAMES.get(key, key.upper().replace(" ", ""))
    if code.startswith("LME:"):
        code = code[4:]
    rid = f"LME:{code}"
    if rid in roots:
        return rid
    raise ValueError(f"unknown LME metal {metal!r}; the LME forwards known are: {_known()}")


def metal_root(root_id: str) -> ContractRoot:
    """The contract root of an LME forward instrument ('LME:CA'), from contract-master."""
    return lme_roots()[lme_root(root_id)]


def lot_tonnes(root_id: str) -> float:
    """Tonnes per LME lot: 25 for copper, aluminium, zinc and lead, 6 for nickel, 5 for tin
    (contract-master's `contract_size`; its size unit must be tonnes)."""
    root = metal_root(root_id)
    if root.size_unit != "t":
        raise ValueError(f"{root.root_id} is sized in {root.size_unit!r}, not tonnes")
    return float(root.contract_size)


def is_lme_instrument(instrument_id: str) -> bool:
    """True for an LME forward's instrument id ('LME:CA'), exactly as stored."""
    return str(instrument_id) in lme_roots()
