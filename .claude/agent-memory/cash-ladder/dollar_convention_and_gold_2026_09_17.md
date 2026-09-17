---
name: dollar-convention-and-gold-2026-09-17
description: Three complaints resolved 2026-09-17 (dollar-convention Position table, XAU excluded from FX Net/Gross, EURSEK split audit) — what was actually broken vs. already correct, and the interpretation implemented.
metadata:
  type: project
---

2026-09-17: user filed three complaints about the delta/exposure/ladder views. Investigated
each against the real dev DB (`data/raw/risk.db`, copied to a scratchpad DB with synthetic
SPOT marks inserted — never write synthetic marks into the real file) before changing
anything. Only one of the three was an actual engine bug; the other two required new
features / regression tests, not bug fixes to existing logic.

**1. "Dollar convention" for AUD/EUR/GBP (and NZD/XAU) — genuinely missing, built new.**
No per-pair "Position" view existed anywhere in the codebase before this (CLAUDE.md's
"Aggregate delta per currency" section names one — "grouped by t.instrument_id in
USD-notional terms" — but it was never implemented). Added
`engine.ladder.ladder.per_pair_delta(conn, as_of_date)` + `pair_delta_totals(df)`, and
`ui.tabs.exposure.pair_position_table` / `pair_position_frame` to render it, wired via
`ui.tabs.cash_ladder._render` (passes `pair_positions=` into `exposure_section`).
Interpretation implemented (stated in code so the user can correct it): every pair gets
TWO differently-signed USD notionals shown side by side — `notional_base` ("Notional
(base ccy)", CLAUDE.md's own "Display notional": sign = base currency's own direction,
+ = bought base) and `notional_usd` ("USD notional (USD sign: + long USD)", the trade's
own USD leg amount unchanged — already USD-direction-signed with no transform needed).
They agree for base_ccy == 'USD' pairs (USDJPY-style) and mirror-flip for quote_ccy ==
'USD' pairs (AUDUSD/EURUSD/GBPUSD/XAUUSD-style). `move_1pct_usd` always follows
`notional_base`'s sign, never `notional_usd`'s — using the dollar-convention-signed
figure for a 1%-move P&L would invert the sign for every quote_ccy == 'USD' pair (long
AUDUSD must gain when AUDUSD rises). For a cross (EURSEK, no USD leg at all) both
columns fall back to the same number: base leg × base currency's OWN ccy→USD spot
(`spot_table`, e.g. EUR's own EURUSD rate) — never a EURSEK→USD rate, which doesn't
exist. `pair_delta_totals` is a genuinely DIFFERENT Net/Gross number from
`portfolio_totals`'s (negated) one whenever a cross is open, since a cross only
contributes its base currency's view per-pair, while `portfolio_totals` independently
captures both legs via the per-currency table — documented in both docstrings; prefer
`portfolio_totals` (negated) for headline cards, `pair_delta_totals` is the Position
table's own sub-total only.

**Important, checked carefully before touching anything: the EXISTING aggregate Net/Gross
USD (`engine.ladder.exposure.portfolio_totals`, negated by every UI caller —
`ui/tabs/header.py::_build_figures` does `usd_position = -ng["net"]`, `ui/tabs/
exposure.py::headline_numbers` and `combined_risk_table` do the same) was ALREADY
dollar-convention-correct for AUD/EUR/GBP before this session — verified by hand
(long-AUD-only book: portfolio_totals.net_usd = +660,000 "long foreign", negated =
-660,000 = "short USD", correct). Summing each currency's own independently-signed
usd_delta and negating the total automatically gets the sign right for both USDXXX and
XXXUSD pairs, because it never has to pick a "USD direction" per pair at all. Do NOT
"fix" `net_gross_usd()` in `ui/tabs/cash_ladder.py` to negate — it deliberately returns
the un-negated net non-USD delta, and `ui/tabs/header.py` already negates it itself;
negating in both places would cancel out back to the wrong sign. If asked to touch this
again, verify the negation chain end-to-end before changing any sign.

**2. XAU (gold) — real bug, fixed.** `engine.ladder.exposure.portfolio_totals` summed
XAU into FX Net/Gross USD like any ordinary currency (its `usd_delta` — oz × XAUUSD spot
— is a huge number, e.g. +$7.29M on the dev DB with a synthetic 3500 spot, entirely
absent from what the header calls "FX Net USD"). CLAUDE.md: "Gold and equity futures are
reported separately." Fixed: added `COMMODITY_CCYS = frozenset({"XAU","XAG","XPT","XPD"})`
to `engine/ladder/exposure.py`; `portfolio_totals(result, *, commodity_ccys=COMMODITY_CCYS)`
now excludes them from `net_usd`/`gross_usd`/`missing` (a missing gold rate no longer
blocks the FX totals) and returns a new `commodities` list (`{currency, local_delta (oz),
usd_delta, status}`) so a caller can show "XAU: 482 oz, $1,688,659" on its own line
without it ever entering the FX sums. `per_pair_delta` tags each pair with a `commodity`
bool too (`XAUUSD.commodity == True`, `.cross == False` — it has a USD leg like any other
XXXUSD pair, it is just not FX); `pair_delta_totals` excludes commodity rows the same way.
`ui.tabs.exposure.combined_risk_frame` tags XAU's row `kind="commodity"` (distinct
styling + a caption under the risk table) instead of removing it from the table — still
visible, just not counted. Checked and found NOT actually broken (contrary to the task's
own hypotheses): no 'XAUUSD' vs 'XAU' spot-lookup key mismatch anywhere (`spot_table`,
`rates_from_marks` both correctly map XAUUSD's base_ccy='XAU' to ccy key 'XAU'); no
formatting bug (ounces just render as a plain number, no currency-specific rounding
applied anywhere in this file).

**3. EURSEK split — audited, found ALREADY CORRECT, added regression tests only.**
`engine/ladder/ladder.py::_DELTA_SQL`/`delta_per_ccy`, `cash_ladder`,
`exposure_adapter.py::records_from_db`/`option_records_from_db`, and
`engine/ladder/exposure.py::build_exposure` all key off `trade_legs.ccy` (or, for
options, `instruments.base_ccy`/`quote_ccy`) directly — a EURSEK forward's two legs
were already landing as independent 'EUR' and 'SEK' rows, never a single 'EURSEK'
currency line, never attributed only to EUR, never dropped for lack of a EURSEK spot
(delta itself needs no spot; only the USD-conversion column does, and that's per-currency
already — EUR via EURUSD, SEK via USDSEK, each independently NaN if missing, never
estimated from a EURSEK cross rate which converts EUR↔SEK, not to USD). Verified against
the real dev DB: `EURSEK` forwards AND `EURSEK...C/P-...` FX_OPTION instruments (base_ccy=
'EUR', quote_ccy='SEK') both split correctly through the whole pipeline including
`_DELTA_SQL`'s FX_OPTION quote-ccy branch (which needs the EURSEK spot itself to convert
the option's EUR-denominated delta into SEK units — that pair IS in the request list
since EURSEK is directly traded — separate from converting SEK to USD, which needs
USDSEK). `tests/test_exposure.py::test_cross_currency_trade_no_usd_leg_priced_at_spot`
already pinned this at the `build_exposure` level before this session. Added DB-level
regression tests in `tests/test_ladder.py` (`test_eursek_splits_into_eur_and_sek_*`) so
this can't silently regress when other agents touch `exposure_adapter.py`/`ladder.py`.

**Flagged, not fixed (outside engine/ladder ownership):** `data/bloomberg/live.py::
build_requests` only requests a SPOT for each pair that has its OWN open trades (e.g.
'EURSEK' itself, if traded) — never the component USD pairs a cross's legs actually need
for USD conversion (EURUSD, USDSEK). On the dev DB this happens not to bite today (EURUSD
and USDSEK are BOTH also directly traded, so their SPOT marks exist anyway) but it is a
real architectural gap: a book that trades a cross with no direct exposure in either
component pair would have that cross's two currencies permanently stuck at
`usd_delta = NaN` (correctly "never estimated", but avoidably missing). `rates_from_marks`
(same file) also explicitly skips any SPOT mark whose pair has neither side USD — so even
if a EURSEK SPOT is being pulled, it's never used for ccy→USD conversion (by design,
correctly — a cross rate can't give a USD rate for either individual currency).
