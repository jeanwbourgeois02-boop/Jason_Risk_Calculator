---
name: bnp-file-quirks
description: Observed quirks of the BNP HA_PNL CSV (blank Fx on zero cash, ES Position=0, positions PK collisions, tolerance maxima, sign of Local Cost) not stated in CLAUDE.md
metadata:
  type: project
---

Quirks of `HA_PNL_20260818.csv` found while building `data/ingest/bnp.py` (2026-09-13):

- CURRENCY rows with a zero balance have blank `Fx` and blank `Trade Factor` (EUR, XAU, HKD, one USD row). Parser writes `fx_to_usd = 0.0` sentinel for non-USD, 1.0 for USD.
- The ESU6 FUTURES row has `Position = 0.0` while `Quantity = 27`; use `Quantity` for contracts. Account is `GSIL-FUT-NMMF`, Price 7768.75, Cost 10,234,475 (= 7581.09 avg fill). Reviewer ruled (2026-09-13) that FUTURES rows must NOT become trades: no fill date / per-fill price in the snapshot, and a date-embedded trade_id would duplicate the position daily. Positions + instrument only; fills come from the xlsx.
- Several forwards share (account, pair, value date) — e.g. 3 x USDTRY 09/16/26 in BNPP-IPB-NMMF — so 239 in-scope rows collapse to 31 `positions` PK keys. `mark` and `Fx` are identical within each group (checked, 0 deviation), so netting is safe. Housekeeper has an open question on "one row per PB position" vs the PK.
- Sign of `Local Cost` = `Quantity x rate` exactly (BUY USD -> positive Local Cost = JPY paid). Synthetic fixtures with the wrong sign fail `local_cost` by 2 x |Q x rate|; always assert `recon.passed` on synthetic files.
- Observed max deviations on the real file, all inside tolerance: local_cost 0.696, mv_local 0.0252, mv_base 14.43 USD (the KRW leg), DTD/MTD identities 6e-5 / 2e-5 USD. Do not loosen tolerances.
- `Strategy` column is "Hybrid" / "Cash Adj"; the contract's HAHY7 / HACA code lives in `NM Strategy`.
- Forwards use `CounterParty` PARIUK for all BNPP accounts; `Trader Name` is a single trader.
- Every forward in the reference file is `SELL x VS .BUY y`; the BUY-first order is untested on real data.
- Blank numeric cells read as NaN via pandas; the recon report treats NaN deviation as a failure and NaN propagates through `max_deviation()` (reviewer W4).
- INTEREST_RATE_SWAP rows (2026-09-15, `data/ingest/irs.py`): all 3 real rows are `IRSOIS-USD-<id>`, account `GSCO-DRV-NMMF`, `Position` always positive in the reference file (no negative/receive-fixed sample seen yet — tested only synthetically). The file carries **no trade date** for IRS (no "TD" token in the description, unlike FORWARD); `trade_date` is set to `effective_date` as a documented placeholder, known wrong for forward-starting swaps. `Quantity` is notional in millions and (in the 3 real rows) always matches `abs(Position)/1e6` exactly. Direction convention used: `Position > 0` -> pay fixed (`trades.quantity > 0`), per explicit task instruction — this is UNVERIFIED by the PM and is the OPPOSITE polarity of the one guessed in the separate "Rates Swap Calculator" reference project (that project's own prefix/direction comments call their guess unconfirmed too, so neither is authoritative). `Symbol Description` rate token is a percent (e.g. "3.98000000") and is stored as a decimal fraction (÷100) in both `trades.price` and the FIXED leg's `rate`.

**Why:** these are not derivable from CLAUDE.md and each one shaped a parser decision.
**How to apply:** when a new daily file arrives, re-check these assumptions first (especially blank Fx and the positions netting) before trusting recon failures. Never reintroduce a synthetic futures trade from the PB file. If a future IRS row ever shows `Position < 0`, verify the pay/receive polarity against the desk before trusting the sign silently.
