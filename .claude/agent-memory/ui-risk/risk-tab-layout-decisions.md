---
name: risk-tab-layout-decisions
description: How the Risk tab (ui/tabs/risk.py) is laid out and why; three cards, caption + Data issues drawer, one VaR-sorted table by sector and commodity, everything else folded (html.Details with counts); built 2026-09-22, Phase A 2026-09-25, one-glance redesign 2026-09-28
metadata:
  type: project
---

Built 2026-09-22 (the PM's nm-dashboard metrics on this book); redesigned 2026-09-25 under
CLAUDE.md "Screens redesign plan" Phase A; cut to "one glance, then unfold" on 2026-09-28
(user: the tab was too dense, seven sections; Jason reads "how much can the book lose?").
The tab renders `engine.risk.book_risk` and margin-limits' results and computes nothing.

**Why:** "Tabs as views" (no UI recompute) and the rule that no figure is blank without its
reason and never zero for a missing input. The 2026-09-24 tab was ~10,000 px tall; the
2026-09-25 one still showed 8 cards, a 15-column table and 5 open sections.

**How to apply (decisions in force, keep unless the user says otherwise):**
- Order (2026-09-28): `book_cards` (3 cards, `cards cards--three`), then `top_block` (the
  caption line + `issues_drawer(issue_items(...), id=ISSUES_ID)`), then `underlyer_section`
  (the one open table), then four folds: `views_section`, `commodity_scenario_section`,
  `scenario_section` (FX), `margin_limits_section`. The housekeeper's brief said both
  "cards on top" and "drawer at the top as today"; cards first was the call taken.
- Cards: exactly 3 children (label, figure line, one clipped note line). VaR (marker
  "excl. N" = `shown_missing` count, hover lists them; the deltas sentence `exposure_words`
  on its hover: commodity net/gross, currency+metal net/gross, FX & cash FX net/gross),
  Blended vol (% of target in the note, target named on hover, flag "over vol target",
  marker "trailing only"), Worst day ex shocks (date + % of cap, flag "over cap", worst day
  raw + date on hover). The old Net/Gross/Commodity net/gross/Worst raw cards are gone:
  their figures live on those hovers and on the Book row's blank Delta USD cell hover.
  A NaN card: "n/a" + reason as hover AND as the note (hover-only reasons were once
  reported as "not working").
- Key table columns (`_columns`): underlyer, sector (a commodity's sector; "Currencies" /
  "Metals"; views "sector view" / "spread view, family"), VaR, blended vol, worst ex
  shocks, delta USD, delta lots (commodity rows only, else None). `part_rows`: commodities
  grouped by sector (sector order = engine order, gross first), each group sorted by VaR
  largest first (`_var_rank`, n/a last, stable), then currencies, then metals; Book pinned
  (`rk.with_footer`, footer filter `{underlyer} = 'Book'`, skip_widths underlyer+sector).
  The rest is on hover: name cell = `underlyer_hover` + `_rest_words` (observations,
  reach, worst raw, carry, note); VaR cell = `_rest_words`; vol cell = trailing/crisis (+
  vol_note; the Book's % of target); worst cell = date (+ % of target, Book: % of cap);
  delta cell = gross (+ reason). Book's delta USD and lots are None with `exposure_words`
  as hover (engine keeps commodity net and currency net apart; the screen sums neither).
- `fold(title, count, hover, children, id=)`: `html.Details` closed, className
  "details details--fold", inline card style (no CSS file touched: ui/assets is ui-shell's
  and was being edited in parallel), summary = `about(title, hover, level="span")` +
  `Span(" (count)", className="fold-count")`. Counts: "4 sectors, 6 spreads"; "17
  scenarios; worst <name> (1.2m)" (`_worst_words`, only when the min is a loss); "margin
  1.2m; 6 of 110 limits set; 1 BREACH" (`margin_limits_count`). Inner sections keep their
  own `about` H4 titles (Margin (estimate, not exchange SPAN), Limits) and every table id.
- Cell rule unchanged: missing = "n/a" string + tooltip reason; not applicable = None blank.
- Limits (user yes 2026-09-25): set checks in the table; every NOT_SET check collapsed into
  one "Not set (N)" line (`not_set_drawer`, id `risk-limits-not-set`), desk then exchange.
- Wording (2026-09-28): "context" / "commodity settlement history (context only, never a
  mark)" instead of "research"; scenario names as config/commodity_stress.yaml gives them.
- No date picker (header as-of store, revision, own safety interval); the one callback's
  output (`risk-body` children) is unchanged.
- Rendering a proof on the sample book: build a scratch db with `import_blotter` from
  data/sample/blotter_sample.csv and call `risk.body(book_risk(conn, d), *risk.margin_and_limits(conn, d))`
  directly; `risk.render` imports `ui.app`, which fails while another agent's app.py is
  mid-edit (seen 2026-09-28, it imported a `pnl` tab that did not exist yet).

Related: [[commodity-sections-2026-09-24]], [[dash-component-behaviour]].
