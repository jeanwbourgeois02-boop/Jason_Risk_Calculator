"""Relative-value spreads (spreads-engine lane, CLAUDE.md "Commodity conversion plan", Phase 3).

For a relative-value trader the spread, not the single fill, is the unit of P&L and risk.
``book_spreads(conn, as_of)`` groups the book's futures into spreads, adds up each spread's P&L
from its trades' ``value_book`` rows (never a new formula: it adds up, it never re-marks) and
reports the outright each spread leaves when its legs do not fully offset.

The grouping rule (written here once, like the FX-swap package rule was in CLAUDE.md):

1. **The user's own grouping wins.** A trade in a bundle (``trades.theme``, else its
   instrument's ``instrument_theme``) is in that bundle's spread, whatever its product, named by
   the bundle (kind ``bundle``). Next, a trade pinned by hand in ``spread_overrides`` (action
   PIN) is in the spread of its ``group_name`` (kind ``pinned``); a trade marked SPLIT there is
   never grouped by the rule below and stays an outright.
2. **Otherwise the rule groups futures** (product FUTURE on a root of ``config/contracts.csv``;
   FX hedges and options are not its business). A trade's lots are first netted per contract
   within its (account, trade date): that net is a *leg*, and every trade of a leg goes where the
   leg goes; a contract that nets to zero there is a round trip, left outright. Then, within one
   trade date, and within one account except as the China-against-West bullet says:
   - **Calendar spread**: two legs of the same root in two different contract months with
     opposite signs and lots equal within 5 % (1:1; the size is the smaller leg, in lots).
   - **Template spread**: legs whose roots are the legs of a template of ``config/spreads/``,
     with signs and sizes that fit its weights: each leg's lots, turned into the spread's
     quantity unit by the template's ``qty_factor`` or by contract-master's unit table (never
     a number per commodity in code, ``templates.py``), divided by its weight, give the same
     spread size within 5 %. Brent against WTI, the 3-2-1 crack, the board crush, SHFE against
     COMEX copper (CNY and USD legs: each leg's own ``value_book`` USD figure, converted at spot
     there), DCE against SGX iron ore.
   - **China against the West** (user yes, relayed by the housekeeper, 2026-09-24): a template
     whose legs are quoted in more than one currency (a CNY leg against a USD leg) may take its
     legs from different accounts of the book, on the same trade date, because onshore Chinese
     futures clear on a separate account. Calendars and single-currency templates stay within
     one account.
   - A trade belongs to one spread only. The same legs matched by several templates are one
     spread, labelled by the first (a calendar first, then file order; the others listed in
     ``also_matches``). Two matches that share a leg are two ways to group the same trades: all
     of them go to **review** (kind ``ambiguous``), none is taken, never guessed.
   - Legs of a calendar or template whose signs fit but whose lots are outside 5 % are listed
     for review (``ratio_off``); so are single-currency ones on the same day on two accounts
     (``accounts``), which the rule does not group. Their trades stay outrights until the user bundles them.
   - Anything else is an **outright**.
3. **Units and currencies.** Legs in different units or currencies are compared only through
   the template's ``qty_factor`` and contract-master's unit table; a spread's USD P&L is the sum
   of its legs' USD P&L as ``value_book`` converted it (at spot). Nothing is hard-coded per
   commodity.

The leftover of a spread is worked out on its OPEN lots: what each leg holds beyond the
largest whole spread the open legs make (``grouping.leftover_lots``), by root, in lots and USD
notional. Zero for a clean spread; the whole remaining leg once the other leg has expired. For a
bundle or a pin it is sized by the calendar or template that uses all its futures legs (closest
ratio), else it is the net lots per root.

Tables: ``spread_overrides`` (``overrides.py``), created defensively here. Its read is wired into
the rule; nothing writes it yet.
"""

from engine.spreads.book import (
    KIND_BUNDLE, KIND_PINNED, PERIODS, REVIEW_ACCOUNTS, REVIEW_AMBIGUOUS, REVIEW_RATIO, SPREAD_PRODUCTS,
    book_spreads,
)
from engine.spreads.grouping import CALENDAR, TOLERANCE
from engine.spreads.overrides import PIN, SPLIT, ensure_overrides_table, override_problems, read_overrides
from engine.spreads.templates import Template, TemplateLeg, load_templates

__all__ = [
    "CALENDAR", "KIND_BUNDLE", "KIND_PINNED", "PERIODS", "PIN", "REVIEW_ACCOUNTS", "REVIEW_AMBIGUOUS",
    "REVIEW_RATIO", "SPLIT", "SPREAD_PRODUCTS", "TOLERANCE", "Template", "TemplateLeg", "book_spreads",
    "ensure_overrides_table", "load_templates", "override_problems", "read_overrides",
]
