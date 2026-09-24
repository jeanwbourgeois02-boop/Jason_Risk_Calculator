---
name: commodity-phase2-removal-2026-09-24
description: Phase 2 removal pass in ui-shell's files (IRS/NDF/swap package/ES-SPX out); FX_SWAP stays; new synthetic sample's 2 deliberate rejects; parallel-lane import breaks
metadata:
  type: project
---

Commodity conversion Phase 2 (user yes 2026-09-24): IRS / rates, NDFs, the blotter's FX-swap
package rule and the equity index (ES, SPX) leave the app, top-down by layer.

- **FX_SWAP stays as a product** (housekeeper relay of ui-header's Handoff, 2026-09-24): manual
  entry books FX swaps, which are FX hedges; only the blotter's package rule leaves. Keep
  "FX_SWAP" in `_PRODUCT_LABELS` and `FX_PRODUCTS_FOR_T1_RATE`; only "IRS" was dropped.
- `revision.trade_set_signature` stays sign-blind even though the IRS direction flip that needed
  it is gone (a flipped sign is still no new trade); only docstrings changed.
- `data/sample/blotter_sample.csv` is now synthetic (45 rows, 43 trades): trade ids 910000032
  (ZCZ6 fits CBOT and ZCE) and 910000033 (root QQ unknown) are deliberate rejects, so the raw
  sample's import is sticky with rejects == 2; tests/test_uploads.py drops those two ids to get
  a clean import.
- Ingest quirk seen: the sample's summary says "31 futures" while only 29 loaded (rejected rows
  counted) and still says "0 rate swaps". ingest-booking's, not ours.

**Why:** parallel lanes edit engine files during the same wave; `ui.app` imports every tab, so a
half-landed engine change (e.g. `engine.risk.history.RATES_FILE` removed before `metrics.py`)
breaks collection of test_ui / test_app / test_uploads / test_ui_revision and makes
`launch.main` return 1 (its ImportError branch).
**How to apply:** a collection ImportError from another lane's module is not our failure: report
it in the Handoff, re-run once the other lane lands.
