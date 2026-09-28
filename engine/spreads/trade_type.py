"""The trade type of a position (spreads-engine, user decisions 2026-09-28).

Jason's broker export labels each trade's strategy, ``PBRoot = JSHY10[.3|.4|.5]_<STRATEGY>``:
``.3`` cross exchange, ``.4`` cross product, ``.5`` term structure, no decimal no type. The
parser stores the decimal as ``trades.trade_type`` (``CROSS_EXCHANGE | CROSS_PRODUCT |
TERM_STRUCTURE | ''``, from the label alone). This module gives every position of
``book_spreads`` its type, source and note from those labels and from what its legs look like:

- **The label wins.** When any leg is labelled and every labelled leg agrees, that is the type,
  source ``label``. Labelled legs that disagree (a ``.3`` and a ``.5`` in one strategy) give no
  type, source ``mixed labels``, the note naming both.
- **No label: inferred** from the open legs' roots in contract-master (``config/contracts.csv``:
  sector, subsector, exchange), source ``inferred``. A leg whose root is of sector ``fx`` (the SGX
  USD/CNH future) or an FX product is a hedge: it is left out of the type and named in the note.
  Several subsectors -> ``CROSS_PRODUCT``; one subsector on several exchanges ->
  ``CROSS_EXCHANGE``; one subsector, one exchange, several roots -> ``CROSS_PRODUCT`` (two
  contracts of one product); one root over several contract months -> ``TERM_STRUCTURE``; one
  root, one month -> ``''`` (an outright). Before the cross rules, roots that each net to zero
  lots across several months are calendars with no product exposure between them: several such
  roots and nothing else -> ``TERM_STRUCTURE`` (Jason's SCO1: HRC Oct/Nov beside iron ore
  Oct/Feb and Nov/Mar). A cross type whose legs also run over several months of one root keeps
  the cross type and says so in the note. With no open leg the closed legs are read instead.
- **The finder's own spreads** take their shape's type (a calendar ``TERM_STRUCTURE``, a
  template of family ``benchmark`` ``CROSS_EXCHANGE``, ``processing`` or ``substitution``
  ``CROSS_PRODUCT``), source ``inferred``; the legs' description is still the note's.
- **Disagreement.** With a label, the inference still runs; where it differs the label stands and
  the note says what the legs look like.

Codes only: ``trade_type`` is one of the three constants or ``''``. The note is a plain sentence
like every reason of this package (it names the types in words, as the brief's own example
does); the screens choose their own display words for the codes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CROSS_EXCHANGE = "CROSS_EXCHANGE"
CROSS_PRODUCT = "CROSS_PRODUCT"
TERM_STRUCTURE = "TERM_STRUCTURE"
NO_TYPE = ""
TRADE_TYPES = (CROSS_EXCHANGE, CROSS_PRODUCT, TERM_STRUCTURE)

SOURCE_LABEL = "label"
SOURCE_INFERRED = "inferred"
SOURCE_MIXED = "mixed labels"
SOURCE_NONE = ""

FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
FX_SECTOR = "fx"

# the finder's shapes: a calendar, or a template's family (config/spreads)
SHAPE_TYPES = {"calendar": TERM_STRUCTURE, "benchmark": CROSS_EXCHANGE,
               "processing": CROSS_PRODUCT, "substitution": CROSS_PRODUCT}

_WORDS = {CROSS_EXCHANGE: "cross exchange", CROSS_PRODUCT: "cross product",
          TERM_STRUCTURE: "term structure", NO_TYPE: "an outright"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_FLAT = 1e-9             # lots: a root whose months net to this is flat, a calendar


@dataclass(frozen=True)
class TypeLeg:
    """One leg as the inference sees it: a contract root (``''`` when the instrument is not on a
    root of contract-master), its contract month ``'2026-10'`` (``''`` when not known), whether
    it is still open, its product and instrument id (for the hedge note)."""

    root_id: str
    month: str
    is_open: bool
    product: str = "FUTURE"
    instrument_id: str = ""
    lots: float = 0.0        # signed; the open lots of an open leg: a root flat across its months is a calendar


@dataclass(frozen=True)
class Inference:
    trade_type: str          # one of TRADE_TYPES or ''
    description: str         # what the legs are: 'copper on COMEX and LME', 'HRC Oct26/Nov26'
    extras: Tuple[str, ...]  # sentences added to the note whatever the type: hedges, the curve inside a cross
    has_legs: bool           # False when no leg could be read (nothing to infer from)


def words(trade_type: str) -> str:
    """The note's words for a code ('cross exchange'; 'an outright' for '')."""
    return _WORDS.get(trade_type, trade_type.lower().replace("_", " "))


def month_word(month: str) -> str:
    """'Oct26' for '2026-10'; the text itself when it is not a year-month."""
    try:
        year, mon = month.split("-")[:2]
        return f"{_MONTHS[int(mon) - 1]}{year[-2:]}"
    except (ValueError, IndexError, AttributeError):
        return str(month or "")


def _product_words(subsector: str, root_id: str) -> str:
    return (subsector or root_id).replace("_", " ")


def _hedge_name(root, leg: TypeLeg) -> str:
    """'USD/CNH' for the SGX USD/CNH future (size unit per currency, from contract-master);
    the instrument id ('USDCNH') for an FX product."""
    if root is not None:
        return f"{root.size_unit}/{root.currency}"
    return leg.instrument_id or leg.product


def infer(legs: Sequence[TypeLeg], roots: Dict[str, object]) -> Inference:
    """What the legs look like, by the rule in the module docstring, from the open legs (all of
    them when none is open). ``roots`` is contract-master's ``load_roots()``."""
    legs = list(legs)
    live = [leg for leg in legs if leg.is_open] or legs
    extras: List[str] = []
    futures: List[Tuple[TypeLeg, object]] = []
    hedges: List[str] = []
    for leg in live:
        root = roots.get(leg.root_id) if leg.root_id else None
        if root is not None and getattr(root, "sector", "") == FX_SECTOR:
            hedges.append(_hedge_name(root, leg))
        elif root is None and leg.product in FX_PRODUCTS:
            hedges.append(_hedge_name(None, leg))
        elif root is not None:
            futures.append((leg, root))
        else:
            extras.append(f"{leg.instrument_id or leg.product} is not on a root of config/contracts.csv, "
                          f"so it is not counted for the type")
    if hedges:
        names = ", ".join(dict.fromkeys(hedges))
        extras.insert(0, f"{names} hedge alongside")
    if not futures:
        return Inference(NO_TYPE, "no futures legs to read a type from", tuple(extras), False)

    months: Dict[str, List[str]] = {}
    net: Dict[str, float] = {}
    order: List[str] = []
    for leg, _root in futures:
        if leg.root_id not in months:
            months[leg.root_id] = []
            net[leg.root_id] = 0.0
            order.append(leg.root_id)
        if leg.month and leg.month not in months[leg.root_id]:
            months[leg.root_id].append(leg.month)
        net[leg.root_id] += float(leg.lots or 0.0)
    by_root = {rid: roots[rid] for rid in order}
    subsectors = list(dict.fromkeys(getattr(r, "subsector", "") for r in by_root.values()))
    exchanges = list(dict.fromkeys(getattr(r, "exchange", "") for r in by_root.values()))

    def contract_words(rid: str) -> str:
        ms = "/".join(month_word(m) for m in sorted(months[rid]))
        return f"{rid} {ms}" if ms else rid

    curved = [rid for rid in order if len(months[rid]) > 1]
    flat = [rid for rid in curved if abs(net[rid]) < _FLAT]
    if len(order) > 1 and len(flat) == len(order):
        kind = TERM_STRUCTURE
        description = " and ".join(contract_words(r) for r in order) + " calendars"
    elif len(subsectors) > 1:
        kind = CROSS_PRODUCT
        description = " and ".join(_product_words(getattr(by_root[r], "subsector", ""), r) for r in order)
    elif len(exchanges) > 1:
        kind = CROSS_EXCHANGE
        description = f"{_product_words(subsectors[0], order[0])} on {' and '.join(exchanges)}"
    elif len(order) > 1:
        kind = CROSS_PRODUCT
        description = (f"{' and '.join(contract_words(r) for r in order)}, two contracts of "
                       f"{_product_words(subsectors[0], order[0])} on {exchanges[0]}")
    elif curved:
        kind = TERM_STRUCTURE
        description = contract_words(order[0])
    else:
        kind = NO_TYPE
        description = contract_words(order[0])
    if kind in (CROSS_PRODUCT, CROSS_EXCHANGE) and curved:
        extras.append("also runs along the curve inside " + ", ".join(contract_words(rid) for rid in curved))
    return Inference(kind, description, tuple(extras), True)


def classify(labels: Iterable[str], inference: Inference) -> Tuple[str, str, str]:
    """(trade_type, type_source, type_note) from the legs' labels and the inference."""
    seen = sorted({str(x or "") for x in labels if str(x or "")})
    extras = list(inference.extras)
    if len(seen) > 1:
        note = "labelled both " + " and ".join(words(x) for x in seen) + ", so no type is taken"
        return NO_TYPE, SOURCE_MIXED, "; ".join([note] + extras)
    if seen:
        label = seen[0]
        if label == inference.trade_type or not inference.has_legs:
            return label, SOURCE_LABEL, "; ".join(extras)
        looks = words(inference.trade_type)
        note = f"labelled {words(label)}; the legs look like {looks} ({inference.description})"
        return label, SOURCE_LABEL, "; ".join([note] + extras)
    if not inference.has_legs:
        return NO_TYPE, SOURCE_NONE, "; ".join([inference.description] + extras)
    if inference.trade_type == NO_TYPE:
        note = f"outright ({inference.description})"
    else:
        note = f"{words(inference.trade_type)}: {inference.description}"
    return inference.trade_type, SOURCE_INFERRED, "; ".join([note] + extras)


def type_fields(labels: Iterable[str], legs: Sequence[TypeLeg], roots: Dict[str, object],
                shape_family: Optional[str] = None) -> dict:
    """``{trade_type, type_source, type_note}`` for a position: the finder's shape (``'calendar'``
    or a template family, ``SHAPE_TYPES``) sets the inferred type when given; else the legs'
    roots do."""
    inference = infer(legs, roots)
    if shape_family in SHAPE_TYPES and inference.has_legs:
        inference = replace(inference, trade_type=SHAPE_TYPES[shape_family])
    trade_type, source, note = classify(labels, inference)
    return {"trade_type": trade_type, "type_source": source, "type_note": note}


def outright_fields(labels: Iterable[str]) -> dict:
    """An outright trade: its own label when it carries one, else no type, no source, no note."""
    seen = sorted({str(x or "") for x in labels if str(x or "")})
    if len(seen) > 1:
        return {"trade_type": NO_TYPE, "type_source": SOURCE_MIXED,
                "type_note": "labelled both " + " and ".join(words(x) for x in seen)}
    if seen:
        return {"trade_type": seen[0], "type_source": SOURCE_LABEL, "type_note": ""}
    return {"trade_type": NO_TYPE, "type_source": SOURCE_NONE, "type_note": ""}
