---
name: lme-forwards-on-ladder-2026-09-24
description: How LME_FWD tickets and listed options sit on the ladder (USD leg only, metal leg never a currency), what delta_per_ccy does with it, and the FUTURE-notional issue in the contract delta SQL awaiting the user's yes
metadata:
  type: project
---

Phase 5 (2026-09-24, housekeeper brief): an LME forward is product LME_FWD on the root id
('LME:CA'), two FX_NEAR legs on the prompt: metal (ccy = root id, tonnes, settles_cash 0)
and USD (-tonnes x fill, settles_cash 1).

- Grid (`cash_ladder`): needed no change; settles_cash filters the metal leg out.
- Records (`exposure_adapter`): `CASH_LEG_ONLY_PRODUCTS = {LME_FWD}` keeps only its
  settles_cash = 1 legs whose ccy is not the instrument id; settled-legs SQL includes
  LME_FWD with `l.ccy <> t.instrument_id`. Realised P&L of LME is never read (the USD leg
  is the cash, as for FX forwards). The metal leg is not "unresolved": it lives on the
  Curve tab.
- USD leg is a USD-row record (like an FX forward's USD leg); portfolio_totals excludes the
  USD row, so FX Net / Gross never move.
- `delta_per_ccy` (_DELTA_SQL, contract SQL) leaves LME_FWD out entirely, so its USD row
  differs from the records' USD row by the LME USD legs. Also its FUTURE branch counts a
  future's NOTIONAL leg as quote-ccy delta (SHFE copper -> CNY). Fix proposed to the user
  via the housekeeper: drop 'FUTURE' from the product list (optionally add
  `OR (t.product = 'LME_FWD' AND l.settles_cash = 1)`). No screen calls delta_per_ccy.

**Why:** CLAUDE.md "LME forwards": "the cash lands on the ladder on the prompt date";
hard rule 7 keeps the contract SQL unchanged without the user's yes.

**How to apply:** if the user approves the SQL change, edit `_DELTA_SQL` and
`tests/test_ladder.py::test_delta_per_ccy_real_file_marks_official_empty`'s expected SQL
together. The sample now has 3 LME_FWD (prompts 2026-09-16, 12-10, 12-16) and 4
CMDTY_OPTION tickets. Same day, second pass (reviewer warning 3): listed options
(valuation.LISTED_OPTION_PRODUCTS) settle from realised_pnl like futures, or are named
unrealised; an open one is neither a record nor unresolved (Curve tab owns its delta).
Never run `py -3 -` in this shell: it opens a REPL that hangs (killed it once).
