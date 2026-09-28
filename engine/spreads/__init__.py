"""Relative-value spreads (spreads-engine lane, CLAUDE.md "Commodity conversion plan", Phase 3).

For a relative-value trader the spread, not the single fill, is the unit of P&L and risk.
``book_spreads(conn, as_of)`` groups the book's futures into spreads, adds up each spread's P&L
from its trades' ``value_book`` rows (never a new formula: it adds up, it never re-marks) and
reports the outright each spread leaves when its legs do not fully offset.

The grouping rule (written here once, like the FX-swap package rule was in CLAUDE.md):

1. **Jason's own strategy is the position** (user, 2026-09-28). A trade whose ``trades.strategy``
   is set (the broker's ``PBRoot`` suffix, ``JSHY10.3_COPAR3`` -> ``COPAR3``, one name per
   trade) is in the position of that name, whatever its product, open or closed (kind
   ``strategy``, spread_id ``STRATEGY-<name>``), and nothing else takes it: not a bundle, not a
   pin, not the rule below. Its legs, P&L and leftover are a bundle's; it has a level only when
   a calendar or template happens to fit all of its futures legs exactly (the bundle's
   ``best_cover``), else ``level_unit`` is '' with the reason.
2. **Then the user's own grouping.** A trade in a bundle (``trades.theme``, else its
   instrument's ``instrument_theme``) is in that bundle's spread, whatever its product, named by
   the bundle (kind ``bundle``). Next, a trade pinned by hand in ``spread_overrides`` (action
   PIN) is in the spread of its ``group_name`` (kind ``pinned``); a trade marked SPLIT there is
   never grouped by the rule below and stays an outright.
3. **Otherwise the rule groups futures** (product FUTURE on a root of ``config/contracts.csv``;
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
4. **Units and currencies.** Legs in different units or currencies are compared only through
   the template's ``qty_factor`` and contract-master's unit table; a spread's USD P&L is the sum
   of its legs' USD P&L as ``value_book`` converted it (at spot). Nothing is hard-coded per
   commodity.

The leftover of a spread is worked out on its OPEN lots: what each leg holds beyond the
largest whole spread the open legs make (``grouping.leftover_lots``), by root, in lots and USD
notional. Zero for a clean spread; the whole remaining leg once the other leg has expired. For a
bundle or a pin it is sized by the calendar or template that uses all its futures legs (closest
ratio), else it is the net lots per root. Beside it (2026-09-28) every spread, position and
outright carries its gross and net USD notional on its open lots (``gross_usd`` / ``net_usd``:
the sum over the legs of |open lots x multiplier x mark x spot|, and the same sum signed, from
the marks and spots the legs' own ``value_book`` rows carry; None with ``notional_reason`` when
a leg has no price or USD conversion, never a partial sum).

Levels (Phase B, 2026-09-25; ``levels.py``): each spread's level at entry, at the previous close
and now, in its quote unit, by the research app's formula (sum weight x leg price converted to the
template's unit + constant; a calendar near - far), the USD a 1.0 move is worth on the open lots,
and the research app's spread_id / instance for it. ``positions``: the same spread put on over
several trade dates as one position, entries averaged by size. Display only: no P&L figure moves.

History (Phase C, 2026-09-25; ``history.py``): ``position_history`` gives one position's LTD USD
(its members' ``value_book`` figures summed, as the periods are) and its level on each date asked,
for the Spreads tab's drill-down chart; ``history_dates`` the business days from its first trade.

Trade type (2026-09-28; ``trade_type.py``): every spread, position and outright carries
``trade_type`` (``CROSS_EXCHANGE`` | ``CROSS_PRODUCT`` | ``TERM_STRUCTURE`` | ''), ``type_source``
('label' | 'inferred' | 'mixed labels' | '') and ``type_note``. The broker's label
(``trades.trade_type``, from ``PBRoot``'s .3 / .4 / .5) wins when its legs agree; with none the
type is read from the open legs' roots in contract-master (several subsectors: cross product;
one subsector on several exchanges: cross exchange; one root over several months: term
structure; one root, one month: an outright), an FX hedge leg left out and named; the finder's
own spreads take their shape's type (a calendar term structure, a benchmark template cross
exchange, a processing or substitution template cross product). A label the legs disagree with
stands, and the note says what the legs look like. Beside them ``strategy`` and ``pb_roots`` (the
distinct raw labels), for the screens' filters. Codes only: the display words are the screens'.

Tables: ``spread_overrides`` (``overrides.py``), created defensively here. Its read is wired into
the rule; nothing writes it yet.
"""

from engine.spreads.book import (
    HAND_KINDS, KIND_BUNDLE, KIND_PINNED, KIND_STRATEGY, PERIODS, REVIEW_ACCOUNTS, REVIEW_AMBIGUOUS, REVIEW_RATIO,
    SPREAD_PRODUCTS, book_spreads, positions_from,
)
from engine.spreads.grouping import CALENDAR, TOLERANCE
from engine.spreads.history import history_dates, position_history
from engine.spreads.levels import research_key
from engine.spreads.overrides import PIN, SPLIT, ensure_overrides_table, override_problems, read_overrides
from engine.spreads.templates import Template, TemplateLeg, load_templates
from engine.spreads.trade_type import (
    CROSS_EXCHANGE, CROSS_PRODUCT, NO_TYPE, SHAPE_TYPES, SOURCE_INFERRED, SOURCE_LABEL, SOURCE_MIXED, SOURCE_NONE,
    TERM_STRUCTURE, TRADE_TYPES, Inference, TypeLeg, classify, infer, outright_fields, type_fields,
)

__all__ = [
    "CALENDAR", "CROSS_EXCHANGE", "CROSS_PRODUCT", "HAND_KINDS", "Inference", "KIND_BUNDLE", "KIND_PINNED",
    "KIND_STRATEGY", "NO_TYPE", "PERIODS", "PIN", "REVIEW_ACCOUNTS", "REVIEW_AMBIGUOUS", "REVIEW_RATIO",
    "SHAPE_TYPES", "SOURCE_INFERRED", "SOURCE_LABEL", "SOURCE_MIXED", "SOURCE_NONE", "SPLIT", "SPREAD_PRODUCTS",
    "TERM_STRUCTURE", "TOLERANCE", "TRADE_TYPES", "Template", "TemplateLeg", "TypeLeg", "book_spreads", "classify",
    "ensure_overrides_table", "history_dates", "infer", "load_templates", "outright_fields", "override_problems",
    "position_history", "positions_from", "read_overrides", "research_key", "type_fields",
]
