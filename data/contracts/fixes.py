"""Applying the Bloomberg check's fixes worksheet to ``config/contracts.csv``.

The bbg-ticker-check lane (``data/bloomberg/ticker_check.py``, ``py 2_launcher.py bbg-check``)
asks Bloomberg what each root really is and writes a worksheet of suggested fixes, one row per
field, with the columns ``WORKSHEET_COLUMNS``. The user marks the rows to take with ``apply`` =
yes; ``apply_fixes`` writes those into the contract file and nothing else.

Rules:

- Only the fields in ``FIXABLE_FIELDS`` can change. ``multiplier`` is never taken from the
  worksheet: it is recomputed from the new contract size, units and price scale, as the loader
  checks it (a wrong multiplier is a wrong P&L).
- A row is refused when its field is not fixable, its root is not in the file, its ``current``
  no longer matches the file (the worksheet is stale: the file changed since the check ran), or
  its ``suggested`` value cannot be a value of that field.
- The whole result is loaded with ``universe``'s own loader before anything is written; if it
  would not load, nothing is written and every row that would have been applied is refused with
  the loader's message.
- The file keeps its column order, row order and LF line endings; rows not fixed are written
  back byte for byte. ``load_roots``' cache is cleared after a write. ``dry_run`` reports the
  same outcome and writes nothing.
"""

from __future__ import annotations

import csv
import difflib
import io
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Union

from data.contracts.tickers import YELLOW_KEYS
from data.contracts.universe import CONTRACTS_CSV, _load, quantity_factor

WORKSHEET_COLUMNS = ("root_id", "field", "current", "suggested", "verdict", "reason", "apply")
FIXABLE_FIELDS = ("bbg_root", "bbg_yellow_key", "bbg_verified", "currency", "contract_size",
                  "size_unit", "quote_unit", "price_scale", "delivery")
_YES = frozenset({"yes", "y", "true", "1"})
_NUMERIC = frozenset({"contract_size", "price_scale", "multiplier"})
_TEXT_CI = frozenset({"bbg_root", "bbg_yellow_key", "currency", "size_unit", "quote_unit", "delivery"})
_BBG_ROOT_RE = re.compile(r"^[A-Z0-9]{1,6}$")
_CCY_RE = re.compile(r"^[A-Z]{3}$")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")


class _BadValue(ValueError):
    """A worksheet value that cannot be a value of its field."""


def _num(text: str) -> str:
    return format(float(text), ".12g")


def _to_bool(text: str) -> bool:
    t = str(text).strip().lower()
    if t in ("true", "1", "yes", "y"):
        return True
    if t in ("false", "0", "no", "n", ""):
        return False
    raise _BadValue(f"{text!r} is not true / false")


def _same(field: str, a: str, b: str) -> bool:
    """Whether two texts are the same value of ``field`` ('1' and '1.0'; 'false' and 'False')."""
    a, b = str(a).strip(), str(b).strip()
    if field in _NUMERIC:
        try:
            return abs(float(a) - float(b)) <= 1e-12 * max(1.0, abs(float(a)))
        except ValueError:
            return a == b
    if field == "bbg_verified":
        try:
            return _to_bool(a) == _to_bool(b)
        except _BadValue:
            return a.lower() == b.lower()
    if field in _TEXT_CI:
        return a.upper() == b.upper()
    return a == b


def _clean(field: str, value: str) -> str:
    """The worksheet's ``suggested`` as the file writes it, or ``_BadValue`` saying why not."""
    v = str(value).strip()
    if field == "bbg_root":
        v = v.upper()
        if not _BBG_ROOT_RE.match(v):
            raise _BadValue(f"Bloomberg root {value!r} is not 1-6 letters or digits")
        return v
    if field == "bbg_yellow_key":
        key = YELLOW_KEYS.get(v.lower())
        if key is None:
            raise _BadValue(f"yellow key {value!r} is not one of {', '.join(YELLOW_KEYS.values())}")
        return key
    if field == "bbg_verified":
        return "true" if _to_bool(v) else "false"
    if field == "currency":
        if len(v) == 3 and v[:2].isupper() and v[2].islower():
            raise _BadValue(f"{v!r} is Bloomberg's minor unit (a price quoted in {v[:2]} cents or "
                            f"pence): fix price_scale (0.01), not the currency")
        if not _CCY_RE.match(v.upper()):
            raise _BadValue(f"currency {value!r} is not a three-letter code")
        return v.upper()
    if field in ("contract_size", "price_scale"):
        if "," in v and not _THOUSANDS_RE.match(v):
            # '0,01' is a decimal comma or a slip; a scale read 100x wrong is a 100x P&L
            raise _BadValue(f"{value!r} is not a number: write it with a decimal point ('0.01')")
        try:
            x = float(v.replace(",", ""))
        except ValueError:
            raise _BadValue(f"{value!r} is not a number") from None
        if not x > 0:
            raise _BadValue(f"{value!r} is not a positive number")
        return _num(str(x))
    if field == "size_unit":
        if not v:
            raise _BadValue("size_unit is blank")
        return v.lower()
    if field == "quote_unit":
        ccy, sep, unit = v.partition("/")
        if not sep or not _CCY_RE.match(ccy.strip().upper()) or not unit.strip():
            raise _BadValue(f"quote_unit {value!r} is not '<currency>/<unit>' such as 'USD/bbl'")
        return f"{ccy.strip().upper()}/{unit.strip().lower()}"
    if field == "delivery":
        v = v.lower()
        if v not in ("physical", "cash", ""):
            raise _BadValue(f"delivery {value!r} is not physical, cash or blank")
        return v
    raise _BadValue(f"field {field!r} cannot be fixed")  # not reached: checked before


def _read_worksheet(path: Path) -> List[Dict[str, str]]:
    """The worksheet's rows, tolerant of encoding (UTF-8 with or without BOM, cp1252), delimiter
    (comma, semicolon, tab, pipe: whichever splits the header into the expected columns) and
    header case; each row carries its line number as ``_line``."""
    raw = Path(path).read_bytes()
    text = ""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = ","
    for d in (",", ";", "\t", "|"):
        names = {h.strip().lower() for h in next(csv.reader([first], delimiter=d), [])}
        if set(WORKSHEET_COLUMNS) <= names:
            delimiter = d
            break
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        raise ValueError(f"{path}: the worksheet is empty")
    header = [h.strip().lower() for h in rows[0]]
    missing = [c for c in WORKSHEET_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"{path}: the worksheet has no {', '.join(missing)} column; expected "
                         f"{', '.join(WORKSHEET_COLUMNS)}")
    out = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        rec = {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}
        rec["_line"] = n
        out.append(rec)
    return out


def _multiplier(rec: Dict[str, str]) -> str:
    size = float(rec["contract_size"])
    scale = float(rec["price_scale"] or 1)
    quote_qty = rec["quote_unit"].partition("/")[2].strip()
    return _num(str(size * quantity_factor(rec["size_unit"].strip(), quote_qty) * scale))


def apply_fixes(worksheet_path: Union[str, Path], *, contracts_path: Optional[Union[str, Path]] = None,
                dry_run: bool = False) -> Dict[str, object]:
    """Apply the worksheet rows marked ``apply`` = yes (yes / y / true / 1, any case).

    Returns ``{'applied': [...], 'skipped': [...], 'refused': [...], 'written': bool}``:

    - ``applied``: ``{'row', 'root_id', 'field', 'before', 'after', 'multiplier_before',
      'multiplier_after'}`` per row taken (``quote_unit_before`` / ``quote_unit_after`` too when
      a currency fix carried the quote unit's currency with it);
    - ``skipped``: ``{'row', 'root_id', 'field', 'apply'}`` per row not marked yes;
    - ``refused``: ``{'row', 'root_id', 'field', 'why'}`` per row marked yes that was not taken.

    ``row`` is the worksheet's line number (the header is line 1). ``written`` is True only when
    the contract file was rewritten. Raises ValueError when the worksheet lacks a column.
    """
    target = Path(contracts_path) if contracts_path else CONTRACTS_CSV
    work = _read_worksheet(Path(worksheet_path))

    with open(target, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    table = list(csv.reader(io.StringIO(text)))
    header, body = table[0], table[1:]
    col = {c: i for i, c in enumerate(header)}
    by_id = {r[col["root_id"]].strip(): r for r in body}
    touched: Dict[str, List[str]] = {}

    applied: List[dict] = []
    skipped: List[dict] = []
    refused: List[dict] = []
    for rec in work:
        root_id = re.sub(r"\s+", "", rec.get("root_id", "")).upper()
        field = rec.get("field", "").strip().lower()
        base = {"row": rec["_line"], "root_id": root_id, "field": field}
        flag = rec.get("apply", "").strip()
        if flag.lower() not in _YES:
            skipped.append({**base, "apply": flag})
            continue
        if field not in FIXABLE_FIELDS:
            refused.append({**base, "why": f"field {field!r} cannot be fixed from the worksheet; "
                                           f"only {', '.join(FIXABLE_FIELDS)}"})
            continue
        row = by_id.get(root_id)
        if row is None:
            close = difflib.get_close_matches(root_id, list(by_id), n=3, cutoff=0.6)
            hint = f"; close matches: {', '.join(close)}" if close else ""
            refused.append({**base, "why": f"unknown contract root {rec.get('root_id', '')!r}{hint}"})
            continue
        now = row[col[field]]
        if not _same(field, rec.get("current", ""), now):
            refused.append({**base, "why": f"stale worksheet: current {rec.get('current', '')!r} but "
                                           f"config/contracts.csv now has {now!r}; run the check again"})
            continue
        try:
            new = _clean(field, rec.get("suggested", ""))
        except _BadValue as exc:
            refused.append({**base, "why": str(exc)})
            continue
        if root_id not in touched:
            touched[root_id] = list(row)
        entry = {**base, "before": now, "after": new}
        if field == "currency":
            qu = row[col["quote_unit"]]
            q_ccy, sep, q_unit = qu.partition("/")
            if sep and q_ccy.strip().upper() == now.strip().upper():
                row[col["quote_unit"]] = f"{new}/{q_unit}"
                entry.update(quote_unit_before=qu, quote_unit_after=row[col["quote_unit"]])
        row[col[field]] = new
        applied.append(entry)

    # the multiplier of every row a fix reached, recomputed from its new values
    for root_id, original in touched.items():
        row = by_id[root_id]
        try:
            mult = _multiplier(dict(zip(header, row)))
        except (ValueError, KeyError) as exc:
            return _refuse_all(applied, skipped, refused,
                               f"the fixed file would not load: {root_id}: multiplier: {exc}")
        if not _same("multiplier", mult, original[col["multiplier"]]):
            row[col["multiplier"]] = mult
        for e in applied:
            if e["root_id"] == root_id:
                e["multiplier_before"] = original[col["multiplier"]]
                e["multiplier_after"] = row[col["multiplier"]]

    if not applied:
        return {"applied": [], "skipped": skipped, "refused": refused, "written": False}

    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows([header] + body)
    fd, tmp = tempfile.mkstemp(prefix=".contracts-", suffix=".csv", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(out.getvalue())
        try:
            _load.__wrapped__(tmp)          # the loader itself, bypassing its cache
        except ValueError as exc:
            return _refuse_all(applied, skipped, refused,
                               "the fixed file would not load: " + str(exc).replace(tmp, str(target)))
        if dry_run:
            return {"applied": applied, "skipped": skipped, "refused": refused, "written": False}
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.remove(tmp)
    _load.cache_clear()
    return {"applied": applied, "skipped": skipped, "refused": refused, "written": True}


def _refuse_all(applied: List[dict], skipped: List[dict], refused: List[dict], why: str) -> Dict[str, object]:
    """Nothing is written: every row that would have been applied is refused with ``why``."""
    moved = [{"row": e["row"], "root_id": e["root_id"], "field": e["field"], "why": why} for e in applied]
    refused = sorted(refused + moved, key=lambda e: e["row"])
    return {"applied": [], "skipped": skipped, "refused": refused, "written": False}
