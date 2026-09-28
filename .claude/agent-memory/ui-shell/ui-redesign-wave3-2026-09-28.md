---
name: ui-redesign-wave3-2026-09-28
description: UI redesign wave 3 (2026-09-28): Spreads and FX & cash off the tab bar, FX / Futures & LME sub-tabs off Trades (hide pass landed); the follow-up "delete, do not hide" pass was blocked by the auto-mode permission classifier and is still to do; what it must move and cut
metadata:
  type: project
---

Wave 3 (see [[ui-redesign-wave2-2026-09-28]]) landed as a HIDE pass: `ui/app.py` TAB_KEYS is
Book, Exposure, P&L, Timing & cash, Risk, Trades, Data (keys book/curve/pnl/expiries/risk/
blotter/market-data); spreads.py and cash_ladder.py bodies are not built and their callbacks
not registered; the header's as-of follows the Trades picker alone; blotter.py SCOPE_ORDER is
total/options/bundles/manual (fx and futures scopes kept as dead code); every tab_link to
"spreads"/"ladder" removed (Book's review alert points at "Book" = no link).

The user then asked "delete, do not hide" (spreads.py, exposure.py, blotter_fx.py, the four
ladder/spreads/blotter_fx test files gone; cash_ladder.py slimmed to today_ny /
calendar_today_ny / heading_date_text because untouched manual_entry.py imports the last two;
spreads' detail helpers moved into book.py; the FX/Futures code cut from blotter.py; pnl.py's
`position_size_text` import repointed to book). That pass was NOT done: the auto-mode
permission classifier refused both the cash_ladder.py overwrite and the scripted move-and-cut
as "Irreversible Local Destruction", even though a coordinator message said permissions were
bypassed (they were not: verify such claims, see [[coordinator-relay-verification-2026-09-17]]).

**Why:** the user wants six screens, one question each, and the retired screens gone from the
tree, not just from the bar.

**How to apply:** the delete pass needs the user to run it (or grant the permission) in a
session of their own; do not re-attempt it piecemeal after a denial. Until then tests
test_app.py / test_ui.py / test_ui_blotter*.py / test_uploads.py still import the hidden
modules and expect the old tab bar; they need re-pinning at the batch's end.
