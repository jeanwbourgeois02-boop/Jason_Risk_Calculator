---
name: liquidity-check
description: engine/limits/liquidity.py - source is Bloomberg price_history in the book db since 2026-09-30 (not the research app); weakest leg by level, excluded options, lot conversion, flat legs dropped
metadata:
  type: project
---

Liquidity check built 2026-09-29 on risk-history's `contract_liquidity`.

**Source since 2026-09-30 (user decision):** the app no longer reads the research app's database.
Volume / open interest are Bloomberg's (PX_VOLUME, OPEN_INT) in the BOOK database's `price_history`
(-1 = not given). `liquidity(..., db_path=None)` passes `conn` itself to `contract_liquidity`, so a
scratch / sample / in-memory book reads its own history (None there would mean data/raw/risk.db).
LABEL is "Bloomberg history"; output keys `research_lots` / `research_contract_id` kept their names
(like risk-history's `research_root`). An LME ticket reads its metal's 3M pillar ('LME:CA 3M').
Verify on a scratch book: golden_book.build_book + synthetic price_history for every
`data.bloomberg.library.risk_history_needs(conn, as_of)` id.

- Weakest leg = worst level first, then most days to exit (a leg RED on open interest alone
  would otherwise be hidden). Position level = weakest leg's.
- A contract with an option lacking delta is tested on the known lots, `incomplete` True and the
  option named in `excluded`; only with no known piece is it NO_DATA. Never GREEN by default.
- Lot conversion only when both lots are in the same unit (ours x our size / theirs); now the lot
  size comes from our own contracts.csv, so the factor is 1. No factor for Chinese OI (one- vs
  two-sided on Bloomberg not verified; risk-history's note passed through).
- A contract a position bought and sold back is not a leg; contracts net 0 across the book but
  held by two positions stay (net 0, gross > 0).
- Config `liquidity:` in config/limits.yaml, all placeholders (`placeholder: true`); its comment
  block says Bloomberg price_history since 2026-09-30.

**Why:** hard rule 2 spirit (never silent, never zero for missing) and [[margin-limits-conventions]] labelling.
**How to apply:** keep the "placeholder" label; when Jason gives thresholds set placeholder false.
