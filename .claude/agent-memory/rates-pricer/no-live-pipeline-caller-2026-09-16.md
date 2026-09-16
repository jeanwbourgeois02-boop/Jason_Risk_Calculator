---
name: no-live-pipeline-caller-2026-09-16
description: engine/rates works correctly end-to-end but nothing in the running app calls bootstrap_and_store/price_and_store outside tests
metadata:
  type: project
---

Audited 2026-09-16 after the user merged rates/IRS work in from another branch and
wasn't confident it worked. Verdict: `engine/rates/` itself has no bugs — 15/15
scoped tests and 483/483 full suite pass, sign conventions match CLAUDE.md
(`quantity > 0` = pay fixed), `PV_USD`/`DV01_USD`/`PAR_RATE` are written with
`source='QL_PRICER'` exactly as `marks_official` expects, and
`test_price_and_store_on_a_real_irs_trade_from_the_reference_csv` proves the full
chain (`bnp.load` → `trades`/`trade_legs` → `price_and_store` → `marks`) works against
the real reference CSV (with a mocked SOFR curve snapshot, since no live curve_quotes
exist in this environment).

**The real gap is orchestration, not a bug**: grepped the whole repo (excluding
tests/docs/memory) for callers of `bootstrap_and_store`/`price_and_store` and found
none — no launcher, ingest pipeline, or `ui/app.py` code path ever invokes them. Other
tabs (FX/futures via `engine.pnl`/`engine.ladder`) compute live on each request, but
`ui/tabs/rates.py` (see its own memory note
`.claude/agent-memory/ui-shell/rates-tab-2026-09-15.md`) was deliberately built to read
`marks_official` directly, never call the pricer live. So on the live `data/raw/risk.db`
(checked 2026-09-16): `curve_quotes` is empty, `curves` is empty, and `trades` has zero
IRS rows (only the BNP CSV has been ingested so far, not the xlsx IRS sheet) — the
Rates tab will render but every row will show blank par_rate/pv_usd/dv01_usd and
recon_status=MISSING until something (a refresh script or the xlsx-IRS ingest path)
populates `curve_quotes` and then calls `price_and_store` per IRS trade. This is outside
`engine/rates/`'s directory (it's a pipeline/data-ingest wiring question) — flagged to
the housekeeper/data-ingest agent rather than fixed here.

**How to apply**: don't re-diagnose this as an engine/rates bug in future sessions —
check first whether `curve_quotes`/IRS `trades` rows actually exist in the live DB and
whether some orchestrator now calls `price_and_store`; if still absent, this is the
same known gap.
