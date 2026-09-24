---
name: bbg-root-guesses
description: How the 102 former 'ZZ' placeholder Bloomberg roots were guessed (2026-09-24) and how the fixes worksheet (data/contracts/fixes.py) corrects them; conventions to keep when adding roots
metadata:
  type: project
---

Phase 2 (user, 2026-09-24: "make the code with the tickers you think are best"): every 'ZZ' root
got a guess, all `bbg_verified` false, every note saying "(low confidence)". Honest basis: no real
recollection for almost any of them, so the rule was
- the exchange code itself (most rows; note "the exchange code, standing in until the Bloomberg check");
- LME ferrous / minor metals: L + LME code (LSC, LHC, LNA, LAA, LCO), after Bloomberg's LP/LA/LX chains;
- a bare code taken by another row's Bloomberg root or exchange code gets a prefix: ZCE Z (ZSM, ZRS,
  ZSH, ZPL, after ZME/ZRO/ZRR), NYMEX N (NJA, NPS), DCE D (DLH), PJM for NYMEX:JM.
- alt roots named in notes only where there was a second real recollection: SHFE:WR alt WIR,
  SHFE:BU alt BIT, DCE:JM alt CKC. No existing non-placeholder root was changed (none known wrong).

**Why:** a bbg root equal to another row's exchange code makes resolve's union match (letter forms
like 'XXZ6-USAA') newly ambiguous; (bbg_root, key) must be unique (the loader refuses otherwise).
**How to apply:** check both collisions when adding or fixing a root. Changing a bbg_root changes
the canonical contract ids ('SSZ26 Comdty'), so instruments / marks under the old id are orphaned:
tell the Bloomberg lanes whenever a fix batch changes roots.

Fixes worksheet (bbg-ticker-check writes it; columns root_id, field, current, suggested, verdict,
reason, apply): `apply_fixes` refuses stale `current`, unknown root/field, bad values (a decimal
comma '0,01' and Bloomberg minor-unit currencies 'GBp'/'USd' are refused: those are price_scale
fixes), recomputes the multiplier, validates the whole file with the loader, all-or-nothing.
