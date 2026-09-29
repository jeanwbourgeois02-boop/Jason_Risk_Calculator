---
name: value-sizing-one-spread
description: 2026-09-29 user rule - RV spreads sized by value not weight; a trade name over two commodities is one spread across months; entry-averaging, front-month level, leftover placement, what Jason's real book became
metadata:
  type: project
---

User decision 2026-09-29 (engine/spreads/strategies.py): an RV commodity spread is sized by VALUE
(lots x multiplier x fill x USD spot of the trade date), and a trade name holding exactly two
commodities is ONE spread across months (`rule` 'one_spread'). Supersedes the physical-unit sizing
of cross pairs in [[strategies-pairs]]; term structures still pair in lots.

**Why:** Jason's CATTLE (FC +91 : LC -167, $15.09m : $14.93m, 1 : 1.84 lots) was split by tonnes
into a pair, a stray FC calendar and two outrights - wrong for an RV book.

Choices I took (coordinator accepted 2026-09-29):
- Value of a side = net lots x average entry, day by day; a day netting to zero on the root (a
  roll) keeps the entry. Gives the brief's exact 15,094,650 : -14,928,200, balance 0.989.
- Not one spread when: '' strategy, one root, 3+ roots (unless exactly 2 subsectors each held one
  way), a root netting to zero (SCO1: calendars), both sides same sign, different sectors with no
  template, or a label (type_source 'label') other than the pair's cross type.
- Balanced = |A + B| <= 10 % of the larger (`VALUE_TOLERANCE`). COPAR3 sits at 0.903: just inside.
- Leftover goes on the heavier side's FRONT leg (largest |lots|), as `leftover_legs` (lots). The
  old `residual_units` rule in engine/curve/leftover.py spoils a side with two same-signed legs
  (COPAR3, ZNA1) until curve-positions reads `leftover_legs`.
- No CNY spot on a trade date -> sizing 'weight' with the reason in the note (ZNA1 on a PC with no
  marks). A template with unequal weights or 3+ legs (`Template.sets_quantity_ratio`) sizes itself.
- The '' catch-all now pairs cross legs by value too: the sample's LME CA/AH pair became 20.1 t CA
  against 75 t AH (was 75 t : 75 t).

**How to apply:** real book after the change: CATTLE, COPAR3, ZNA1 one spread each; SCO1, STEEL,
SILARB1 unchanged. Verify with a direct call on a copy of data/raw/risk.db (no marks: value at mark
is None, CNY value None).
**Amended 2026-09-29 (user: "yeah lets do that"):** same commodity (subsector) on two exchanges and cracks (families
crude vs products) are sized by PHYSICAL quantity (SIZING_PHYSICAL, physical_rule, in _sizing and _one_spread); value
only between different commodities (CATTLE, crush). The balanced flag stays on dollar value. Moved leftovers: IRON1 0 t,
BRWTI1 0 bbl, CRACK1 gasoil/gasoline 0, COPAR1 -104.64 t, GOLDJP1 -3.55 oz, TTFNBP1 NBP -1.449 lots, real COPAR3 35.55 t.
Proof: snap.py/snapdiff.py in scratch (value_book identical, curve diffs only leftover keys, trade_book P&L identical).
