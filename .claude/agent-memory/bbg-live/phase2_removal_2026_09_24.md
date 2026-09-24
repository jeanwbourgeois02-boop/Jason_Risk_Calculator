---
name: phase2-removal-2026-09-24
description: Commodity Phase 2 (2026-09-24) removal in live.py - status["rates"] became status["curves"] (OIS quotes + bootstrap, no swaps), timing key "curves", NDF/dividend/index/fixings gone, manual/CSV mark types trimmed; what was kept and why; scratchpad traps
metadata:
  type: project
---

User approved 2026-09-24 that rates/IRS, NDFs and the equity index leave the app (CLAUDE.md
"Commodity conversion plan", Phase 2, layer 6 market data).

**What the pull is now:** contract dates -> SPOT / FWD_OUTRIGHT / FUTURE_PX (library.MARK_KINDS
only) -> `_curves_step` -> `_vol_step` -> `_options_step` (FX options only; the listed
EQ_OPTION's price is still pulled as FUTURE_PX with mid_first, but no equity Greeks) -> ledger.

**status["curves"]** (was status["rates"]; ui-market-data stopped rendering "rates"):
{as_of_date, currencies: {ccy: {quotes, nodes, error}}, bootstrapped (int), seconds:
{bloomberg, bootstrap}} + "skipped" ("no FX_OPTION needs an OIS curve") or "error". Currencies
from `library.keys(conn, today, "OIS_CURVE")`; bootstrap via
`engine.rates.store.bootstrap_and_store` (import guarded). TIMING_KEYS "rates" -> "curves".
`pull_once(..., rates_source=...)` keeps its parameter name (a rates_marketdata source).

**NDF tenor families:** removed later the same day once bbg-backfill confirmed nothing of its own used them; `tenor_ticker` stays (backfill._tenor_tickers imports it) and is the pair spelling only.

**Trimmed:** manual.MANUAL_MARK_TYPES = SPOT, FWD_OUTRIGHT, FUTURE_PX, DELTA, PREMIUM
(write_manual_mark raises ValueError otherwise; the UI shows "Save failed"); marks_csv.MARK_TYPES
the same five. `_ensure_fx_instruments` writes is_ndf 0 always. pull_marks --probe lost its two
ESU6 steps.

**Traps:** the scratchpad is shared with other lanes' agents (edit.py, splice.py, edit_tests.py
exist from them) -- prefix scripts `bbglive_`. A bash heredoc holding a long Python script with
mixed quotes failed ("unexpected EOF") -- write scripts with the Write tool. Other lanes edit
ingest mid-run, so a test importing the blotter can fail transiently (IRS_DIRECTION_DDL,
FUTURE_SYMBOL_RE); rerun before blaming own code.
