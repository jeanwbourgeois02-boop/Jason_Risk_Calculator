---
name: diagnostics-tool-already-built
description: tools/bbg_diagnostics.py (re-exported by data/bloomberg/bbg_diagnostics.py, run by 3_diagnostic.py and the Data tab's "Check Bloomberg connection" button) already covers FX, futures, OIS, FX options AND, since 2026-09-28, the commodity steps (roots verified, no-ticker futures, contract dates, options on futures, LME curves, conversion spots, the last pull's commodity blocks); don't rebuild, extend
metadata:
  type: project
---

`tools/bbg_diagnostics.py::run_bloomberg_diagnostics(db_path=None, as_of=None, host=None, port=None)
-> list[{"name","status":"pass"|"fail"|"warning","message"}]` is the one diagnostics
implementation (bbg-diagnostics owns it although it lives under `tools/`; infra owns its test
`tests/test_bloomberg_diagnostic.py`, which actually tests `tools/bloomberg_terminal_probe.py`,
not this module). `data/bloomberg/bbg_diagnostics.py` re-exports it for `ui/tabs/header.py` /
market_data's button; `3_diagnostic.py` is the CLI wrapper.

Row shape is the established UI contract: `name` / `status` / `message`, one plain sentence,
no tracebacks. Reuse it; never invent a second shape.

**Commodity checks added 2026-09-28 (user-approved), in the order the panel shows them,
between "Futures price coverage" and "OIS curve coverage (FX options)":** Contract roots
verified, Futures with no Bloomberg ticker, Contract dates stored, Options on futures (4 rows:
price / underlying price / discount curve / Greeks), LME curves + LME prompt outrights,
Conversion spots; and "Last pull: commodity steps" (contract dates / LME curves / not
requested / options on futures) after "Past closes (backfill)". Each reads
`library.needed_on(conn, as_of, include_unrequestable=True)` (works on a read-only handle:
`library.rows` falls back to an in-memory compute) and the app's own "on file" rules
(`inventory.lme_curve_status`, `inventory.contract_dates_inventory`, `engine.lme.forward_at`,
`live.not_requestable_futures`, `live.contract_dates_summary`, `live.lme_summary`). Each gives one
clean pass row when the book has none of that kind; a crash becomes one warning row
(`_warn_on_crash`), unlike the older checks whose `_safe` wrapper gives a fail row.

**Severity rule chosen:** an option on a future's own FUTURE_PX missing = fail (P&L); its
underlying price / OIS curve / Greeks missing = warning (Greeks only). An LME prompt not
stored but readable between pillars = warning; beyond the last pillar = fail. Since 2026-09-28
the FX spot / forward rows leave LME instruments out (`engine.lme.is_lme_instrument`) so an
LME prompt is judged by the LME rows, not failed with an FX-worded reason.

**How to apply**: extend this module, never duplicate a check elsewhere. Verify with ruff,
`py -3 3_diagnostic.py` (connectivity FAIL expected off the Bloomberg PC) and
`py -3 3_diagnostic.py --db data/raw/sample.db` (the sample book has every product kind, so
it exercises all the commodity rows; `data/raw/risk.db` is usually empty and only shows the
"nothing of that kind" pass rows).
