"""Bloomberg tickers of an LME curve (lme-forwards lane), for bbg-curves to pull.

Every ticker here is a best guess until the Bloomberg check confirms it on a terminal:

- cash: 'LM<code>DY Comdty' ('LMCADY Comdty'), Bloomberg's LME cash price. Written as the
  metal's SPOT (settle_date = as_of_date, the contract's SPOT key); the P&L places it at the
  cash date.  # unverified
- 3 months: 'LM<code>DS03 Comdty' ('LMCADS03 Comdty'), the rolling 3-month price, a
  FWD_OUTRIGHT at the 3-month date of the curve's day.  # unverified
- monthly: the dated third-Wednesday contract on contract-master's Bloomberg root, in
  Bloomberg's live one-digit-year form ('LPV6 Comdty' for copper October 2026), a
  FWD_OUTRIGHT at that month's prompt. The dated form is used rather than the generics
  ('LP1 Comdty') because a generic's month depends on Bloomberg's own roll rule, which is not
  known here, and a pillar on the wrong date is a wrong mark.  # unverified: that Bloomberg
  keys LME third-Wednesday contracts by month code like a futures chain, and the roots
  (LP, LA, LX, LL, LN, LT; the others are contract-master's low-confidence guesses)
"""
from __future__ import annotations

from data.contracts.tickers import month_code, padded_root
from engine.lme.metals import lme_root, metal_root
from engine.lme.prompts import DateLike, _d, prompt_structure

__all__ = ["lme_curve_tickers", "cash_ticker", "three_month_ticker", "monthly_ticker"]


def cash_ticker(root_id: str) -> str:
    """'LMCADY Comdty' for 'LME:CA'.  # unverified"""
    return f"LM{lme_root(root_id)[4:]}DY Comdty"


def three_month_ticker(root_id: str) -> str:
    """'LMCADS03 Comdty' for 'LME:CA'.  # unverified"""
    return f"LM{lme_root(root_id)[4:]}DS03 Comdty"


def monthly_ticker(root_id: str, year: int, month: int) -> str:
    """'LPV6 Comdty' for 'LME:CA' October 2026.  # unverified"""
    root = metal_root(root_id)
    return f"{padded_root(root.bbg_root)}{month_code(month)}{int(year) % 10} {root.bbg_yellow_key}"


def lme_curve_tickers(root_id: str, as_of: DateLike) -> list[dict]:
    """[{ticker, pillar_date, kind, mark_type, settle_date}] of the LME curve of `root_id`
    quoted on `as_of`, one per pillar of ``prompt_structure(as_of)``, sorted by date:

    - kind 'CASH': mark_type 'SPOT', settle_date = `as_of` (the SPOT key), pillar_date = the
      cash date;
    - kind '3M' and 'MONTHLY': mark_type 'FWD_OUTRIGHT', settle_date = pillar_date = the
      prompt.

    Dates are ISO strings. An unknown metal raises ValueError naming the known ones."""
    rid = lme_root(root_id)
    day = _d(as_of)
    out = []
    for p in prompt_structure(day):
        if p.kind == "CASH":
            ticker, mark_type, settle = cash_ticker(rid), "SPOT", day
        elif p.kind == "3M":
            ticker, mark_type, settle = three_month_ticker(rid), "FWD_OUTRIGHT", p.date
        else:
            year, month = (int(x) for x in p.label.split("-"))
            ticker, mark_type, settle = monthly_ticker(rid, year, month), "FWD_OUTRIGHT", p.date
        out.append({"ticker": ticker, "pillar_date": p.date.isoformat(), "kind": p.kind,
                    "mark_type": mark_type, "settle_date": settle.isoformat()})
    return out
