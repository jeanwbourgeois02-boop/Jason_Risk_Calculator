---
name: phase5-options-lme-library-2026-09-24
description: How the Bloomberg library lists options on commodity futures (CMDTY_OPTION) and LME forwards (LME_FWD) - roles, kinds, the include_lme guard, pillar trimming, LME curve completeness rule - and why
metadata:
  type: project
---

Commodity conversion Phase 5 (2026-09-24), LIBRARY_VERSION "2026-09-24.3".

- **Option on a future** (any listed option whose base_ccy is a contract root; EQ_OPTION on a non-root keeps the old SPX-ticker path): FUTURE_PX on its own id (instruments.bbg_ticker), CONVERSION SPOT if non-USD, UNDERLYING FUTURE_PX (underlying's canonical id from `option_for(root, id)` WITHOUT conn, keyed at the underlying's instruments.expiry_date, needed until the option's expiry), one OIS_CURVE (`option_discount_ccy`: ccy if in both OIS_INDEX and engine.rates.conventions.CCY_RFR, else USD), CONTRACT_DATES on the option id (fields OPT_EXPIRE_DT / LAST_TRADEABLE_DT via `contract_dates_needed` entry `fields`, plus a `product` key).
- The brief first said underlying + curve "today only"; mid-task options-store asked for them on every day open (price_close prices past Greeks). So they are historical: HISTORY_INPUT_PRODUCTS = ("FX_OPTION",) + LISTED_OPTION_PRODUCTS, and role UNDERLYING is an ordinary historical FUTURE_PX.
  **Why:** otherwise past-close CMDTY Greeks skip with "no curve USD" when no FX option brings USD in.
- **LME forward**: instrument id = root id 'LME:CA' (asset_class LME_FWD in ingest). SPOT (cash ticker), FWD_OUTRIGHT at prompt with ticker '' and LME_CURVE with ticker '' - both requestable unless the root is unknown/placeholder (`_lme_curve_reason`).
- `needed_in_range(..., include_lme=False)` by default drops LME rows.
  **Why:** the backfill's FX paths treat every SPOT/FWD key as a pair ('LME:CA1M Curncy' tenor tickers, 15:00 bars) - a wrong ask (hard rule 8). The backfill opts in from its LME step.
- 'LME:CA' is 6 characters: `live._ensure_fx_instruments` would take it for a pair; sync excludes LME keys.
- LME pillars are trimmed (`lme_curve_pillars(root, day, through=furthest prompt)`): cash, 3M, monthlies up to the first on/after the furthest prompt (27 months otherwise).
- Inventory rule: an LME curve is complete when cash SPOT (settle=day) and 3M FWD_OUTRIGHT (the day's 3M date) are official, each passing `is_close_row` on a past day. Item mark_type 'LME_CURVE', missing items carry a string `detail` (never a list: Dash tables).
- LME closes are 17:00 NY: inventory passes `instrument_id=` to `backfill.is_close_row` (bbg-backfill added it the same day). `live.build_requests` no longer returns LME rows (bbg-live has its own LME step); the inventory still lists LME SPOT/FWD as needed.
- Open at hand-off: nobody writes the prompt-date FWD_OUTRIGHT yet (so it shows MISSING); live's listed-option mid path keyed on asset_class EQ_OPTION.

Related: [[commodity-futures-library-2026-09-24]], [[macro-needs-retired-2026-09-24]]
