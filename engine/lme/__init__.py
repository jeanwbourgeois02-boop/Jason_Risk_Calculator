"""LME forwards (lme-forwards lane, commodity conversion plan Phase 5).

LME base metals trade as forwards to a prompt date (daily to three months, Wednesdays to six,
third Wednesdays beyond), marked at the outright for the ticket's own prompt and settled on
it, like an FX forward. The P&L rule, approved by the user on 2026-09-24, is CLAUDE.md
"P&L conventions -> LME forwards"; pnl-valuation wires it, this package gives it everything
LME-specific:

- `prompts`: the prompt calendar on the LME calendar (`cash_date`, `three_month_date`,
  `third_wednesday`, `is_valid_prompt`, `prompt_structure`);
- `metals`: which contract roots are LME forwards (`lme_root`, `lot_tonnes`,
  `is_lme_instrument`); an LME forward's instrument id is its root id ('LME:CA');
- `tickers`: the Bloomberg tickers of a day's curve (`lme_curve_tickers`);
- `curve`: the official curve of a day, a prompt's forward on it, the settlement price.

Nothing here asks Bloomberg for anything or writes to the database.
"""
from engine.lme.curve import day_curve, forward_at, settlement_price
from engine.lme.metals import is_lme_instrument, lme_root, lme_roots, lot_tonnes, metal_root
from engine.lme.prompts import (
    LME_CALENDAR,
    MONTHLY_PILLARS,
    Pillar,
    cash_date,
    is_valid_prompt,
    monthly_prompt,
    prompt_structure,
    prompt_zone,
    six_month_date,
    third_wednesday,
    three_month_date,
)
from engine.lme.tickers import cash_ticker, lme_curve_tickers, monthly_ticker, three_month_ticker

__all__ = [
    "LME_CALENDAR", "MONTHLY_PILLARS", "Pillar", "cash_date", "cash_ticker", "day_curve",
    "forward_at", "is_lme_instrument", "is_valid_prompt", "lme_curve_tickers", "lme_root",
    "lme_roots", "lot_tonnes", "metal_root", "monthly_prompt", "monthly_ticker",
    "prompt_structure", "prompt_zone", "settlement_price", "six_month_date", "third_wednesday",
    "three_month_date", "three_month_ticker",
]
