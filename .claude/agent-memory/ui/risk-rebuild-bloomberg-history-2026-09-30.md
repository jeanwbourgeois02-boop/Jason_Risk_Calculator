---
name: risk-rebuild-bloomberg-history-2026-09-30
description: Risk tab rebuilt simple (headline sentence, 5-column table, worst stresses card, 3 folds, drawer, empty card) and every research-app trace removed from ui/ once risk history became price_history in the book DB; scratch-book + shot recipe
metadata:
  type: project
---

2026-09-30. User on the Risk tab: "nothing work - it looks shit and i dont even understand what its trying to do";
research app cut out ("linking the two apps ... best avoided"). Supersedes [[risk-trade-table-phase-g-2026-09-29]],
[[layout-wave2-risk-2026-09-29]] and [[risk-four-blocks-2026-09-29]] for layout.

**Risk now** (`ui/tabs/risk.py`): headline card = one sentence "On a bad day (1 in 20) the book can lose about $X"
(subset_var of rows showing, inside a `tk-headline` div so ui-check places it) + "No limit set" while
vol_target_placeholder; table Trade | What it is (book._what_td / book._with_words) | Daily risk | Share of book |
Hedged (words from hedge %: >=80 Well, >=40 Partly, >=0 Barely, <0 Adds risk, one leg Outright; not-included row =
dash). No Group switch on Risk (`trade_filter.NO_GROUP_TABS`). Panel = two kv tables with Thead titles (Figures;
Worst stresses on this trade via `risk_folds.trade_stress`). `risk_folds.stress_card` (always shown, top 5, line
open key "stress:<name>" in FOLDS_STORE via FOLD_TYPE ids) + folds net / currency / greeks. Replays with no figure
go to the drawer (`stress_issues`). Empty state = `empty_card` when `price_history` is empty; the table card
(CARD_ID) hidden by the render callback's style output. FOLD_PRICE / FOLD_STRESS_MORE kept only because
tools/ui_check.py names them.

**Shared:** `blotter_pricing.price_history_summary(conn)` (COUNT/MIN/MAX, memo per revision),
`config_inputs_key()` replaced research_inputs_key, `trade_filter.NO_HISTORY_TEXT` / `is_no_history_reason` (Book and
Risk say "no price history" once). Book hides z while no history and no z. Header `_risk_inputs(conn)` passes the
active DB to load_history and load_commodity_history. Data: `data_checks.history_problems` reads
status["backfill"]["risk_history"]; `market_data.history_line` on the Bloomberg card.

**Traps:** book-grid sticky thead inside an overflow slot covers the first row (static head in `.risk-stress-card`).
The engine's stress["reasons"] repeat per-scenario reasons with "n/a": filtered by scenario-name prefix. plain_ids
does not translate one-digit live tickers ("CLZ6 Comdty"): replace the ticker by the name before showing.

**Recipe** (scratchpad `risk/`): `mk.py` builds with.db / empty.db (golden book through today + random-walk
price_history over `library.risk_history_needs`, one factor per subsector so spreads hedge); `chk.py <db> tabs`
monkeypatches tools.ui_check build_sample/AS_OF to run ui-check's rules on a book WITH history (ui-check's own
sample has none); `shot.py` needs `.venv/Scripts/python.exe` (playwright, channel="chrome"), `?as_of=` pinned.
