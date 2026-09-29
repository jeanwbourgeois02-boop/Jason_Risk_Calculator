---
name: layout-wave2-risk-2026-09-29
description: Layout wave 2 on the Risk tab (2026-09-29) - headline cut to VaR / of vol target / diversification / lowest hedge, research_head per column, row "i" for trades not in the risk, days_text, panel kv, FX totals in tfoot, price check Gap/Allowed, not-set drawer through issues_drawer; shot recipe
metadata:
  type: project
---

Risk after layout wave 2 (ui-check --strict --tab risk exit 0, 12 DESIGNED_LINE kept: headline items, history line, fold titles).

- Headline (`risk.headline`): only VaR 1 day 95 % (with its one Excl. N), Of vol target, Diversification (engine `diversification_usd`, alone/together on hover), Lowest hedge. Trade count, daily risk, leftover, FX unhedged are the Book row's / header's. The mock caveat is said once, in `risk-reach`.
- Research columns: `tf.research_head(research)` in the heading, never `research_mark`.
- Trade rows: `row_reasons` -> `row_info` after the name (not in the risk figures + why; counted without its hedge). The visible " without hedge" badge is gone.
- Leftover zero = blank cell with hover (as FX unhedged "not applicable"). Days to exit `days_text` (one decimal, "< 0.1"), `days_exact` on hover; legs table the same.
- Question sentence lives in the "Risk by trade" title hover. Margin line (while placeholder) = a drawer row ("Margin and limits", "", ...).
- `issue_items` returns (kind, where, reason) triples. `risk_limits.not_set_drawer` = `issues_drawer(title="Not set", id=LIMITS_NOT_SET_ID)`, `_not_set_item` returns (text, why).
- Panel: legs table in `.tk-legs-main` + facts `tk-kv risk-kv` inside `.tk-panel-legs`.
- Currency fold FX table: metals as rows ("Not in the net"), Net USD / Gross in `html.Tfoot` (tfoot is styled as totals, never sticky). Price check: sentence on the count's hover and each row's on its Check cell; columns Gap (engine `gap_pct`) and Allowed (`threshold` is a FRACTION, 0.2).
- CSS in `/* wave 2: risk */` at the end of style.css.

**Shot recipe:** scratchpad `w2risk/shot_risk.py` (build_sample + create_app + werkzeug; click the Risk tab text, wait for a digit in `#risk-table tr.tk-row td:nth-child(3)`, click COPAR1 td nth(2), fold heads by `[id*='"idx":"currency"']`, `#risk-issues > summary`). Full-page shots paint the sticky top bar / table head mid-page: an artifact, not layout. No PIL in .venv.
