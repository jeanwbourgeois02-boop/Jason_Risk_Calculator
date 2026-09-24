---
name: phase3-fx-only-net-gross-2026-09-24
description: Ladder headline card and risk-table Net/Gross are FX only since Phase 3 (book-positions request); futures keep USD delta in the open futures table; pointers to Curve/Risk tabs
metadata:
  type: project
---

2026-09-24, commodity conversion Phase 3 (request from book-positions via the housekeeper).
The Ladder's headline card ("USD delta, FX only", `exposure.HEADLINE_TITLE`) and the risk
table's footer (`NET_FOOTER_LABEL`, `GROSS_FOOTER_LABEL` = "Gross USD delta, FX only") no
longer add `futures_usd_delta`'s value. `headline_numbers` dropped its `futures` argument
(signature now `(result, fallback_ccys=None, forward_proxy_ccys=None)`); a future with no
price cannot blank either total any more, only a missing FX rate can.

**Why:** CLAUDE.md "Net USD": commodity futures are positions on the Curve tab, not in Net /
Gross USD. The Ladder card now equals the header's Net / Gross (`cash_ladder.net_gross_usd`,
already FX only).

**How to apply:**
- Futures still show their USD delta in the open futures table AND as rows of the risk table
  (not summed into its footer). Under the futures table: `futures_note()` (id
  `FUTURES_NOTE_ID`) points to the Curve tab and to the Risk tab for commodity scenarios.
- `FUTURES_NO_SCENARIO` now ends "commodity scenarios: see the Risk tab"; commodity stress
  (`engine.stress.commodity_stress`) is rendered by ui-risk, never on the Ladder.
- The Phase 2 leftovers from the brief (CNH xfail, `futures_pct_by_scenario` on
  `combined_risk_frame`) were already done in Phase 2; see [[phase2-macro-removal-2026-09-24]].
- The PostToolUse lint hook fails when the session cwd is `.claude/agents` (relative
  `tools/lint_hook.py`); ruff is not installed on this PC either. Compile-check instead.
