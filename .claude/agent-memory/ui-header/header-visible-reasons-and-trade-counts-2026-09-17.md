---
name: header-visible-reasons-and-trade-counts
description: header.py fix for "the headline doesn't work" -- figures were already computing correctly on a zero-official-marks DB, the reason was hover-only and generic; now visible + specific via data.bloomberg.inventory
metadata:
  type: project
---

2026-09-17 investigation (user complaint: "the headline things ... none of them work").
Built the app headlessly and dispatched `header-block-figures.children` through the real
Flask `/_dash-update-component` endpoint (not just calling `_build_figures` in Python) for
both 2026-09-17 (zero marks at all) and 2026-08-17 (BNP_BVAL-only marks) on the dev DB
(`data/raw/risk.db`, 772/237 trades respectively at those as-of dates via `value_book`'s
own `trade_date <= as_of` filter -- the DB's raw `trades` COUNT(*) is 1004, don't confuse
the two). **Finding: no callback bug.** Every P&L period correctly resolved to "n/a" with
a `reason`; Net USD delta / Gross USD delta (computable from trade legs + spot alone, no
marks needed) were already populating correctly both dates. The actual defect: `_pnl_card`
only ever put the `reason` in an HTML `title` tooltip -- invisible until hover -- and the
reason text itself was generic ("today's LTD unavailable") rather than actionable.

Fix (`ui/tabs/header.py`): `_pnl_card` now also renders the reason as a visible caption
`html.Div` under the muted "n/a" value (kept "n/a" + the tooltip too, for backward compat
with `tests/test_ui.py::test_header_pnl_card_unavailable_shows_reason_as_tooltip`, which
this agent does not own and did not edit -- see [[coordinator-relay-verification]]).
`_missing_marks_reason(conn, as_of)` builds a concrete sentence from
`data.bloomberg.inventory.mark_inventory` (DB-only read, no Bloomberg connection opened --
safe to call from a UI callback): "no official SPOT/FWD_OUTRIGHT for 2026-09-17 (26 of 26
needed marks) — run the Bloomberg pull". `_resolve_reason` substitutes this into every
cascading period card (Daily/5d/MTD/YTD all ultimately blocked by the SAME today's-LTD gap,
or by a *different* reference date's gap -- it resolves per the actual blocking date, not
always `as_of`). Added a "Trades" card (open/settled counts) that is always computable.

**How to apply:** if this pattern of "figure computes fine, reason is just invisible/vague"
recurs elsewhere (Blotter strips, Rates recon status, Options), the same two-part fix
(visible caption + `mark_inventory`-derived specific reason) is the template -- but note
`mark_inventory`/`marks_official` share a blind spot with the stale-view bug, see
[[stale-marks-official-view-2026-09-17]].
