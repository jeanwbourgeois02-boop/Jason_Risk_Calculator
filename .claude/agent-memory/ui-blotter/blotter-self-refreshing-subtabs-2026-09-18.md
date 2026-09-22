---
name: blotter-self-refreshing-subtabs-2026-09-18
description: Options and Rates sub-tabs are no longer rebuilt on a marks-only revision (their modules refresh in place); the strip above them follows via _register_strip_refresh; the Options strip hides cards waiting for an earlier close's option marks; residual whole-sub-tab rebuild after every saved strike; tests that still open the live database
metadata:
  type: project
---

2026-09-18 integration round (lane: ui/tabs/blotter.py, tests/test_ui_blotter.py,
tests/test_ui.py). Follows [[realised-pnl-misaligned-insert-2026-09-18]].

**Who rebuilds what on a revision (ui/revision.py).** `blotter._update` rebuilds the
showing sub-tab on a BOOK revision, a date change or a sub-tab change. On a DATA-only
revision it rebuilds only `_MARKS_REBUILD_SCOPES = ("bundles", "fx")`. Total book and
Futures refresh rows in place (`_apply_filters`); `_SELF_REFRESHING_SCOPES = ("options",
"rates")` refresh their own table from both revision stores inside their own modules
(`options._render` / `_headline`, `rates._refresh`), and the one piece of those sub-tabs
built in blotter.py -- the generic P&L strip `blotter-strip-<scope>` -- follows through
`_register_strip_refresh` (Input DATA_REVISION_ID only). `rates.register_callbacks` was
never called from anywhere before this round, so the Direction dropdown did nothing.

**Residual, reported not fixed.** `revision.book_signature` includes
`TOTAL(instrument_options.strike)` and `TOTAL(trades.quantity)`, so a strike typed into an
Options cell (or a Direction flipped under Rates) is a BOOK revision: 5-10 s later the
whole sub-tab is rebuilt anyway. A user typing several strikes in a row can still be
interrupted. Fixing it needs the banners (`missing_terms_notice`, `bad_values_notice`) in
an always-present container with their own in-place refresh, THEN a terms-only book change
can skip the rebuild -- without that, skipping it would leave "N options cannot be priced"
on screen after the strike was entered. Both banners are static today: they change only
on a rebuild.

**Options strip rule (`options_hidden_cards`).** Option PREMIUM marks exist only for days
the app ran with Bloomberg (the backfill writes SPOT / FWD_OUTRIGHT / FUTURE_PX, never
PREMIUM), so Daily / Previous day / 5d / MTD / LTD-1 / LTD-2 are "n/a" at first and the
user reads a row of n/a as "the headlines don't work". A card is hidden only when it is
unavailable AND every unpriced option row on each EARLIER date it needs has
`value_book`'s own reason prefix "no PREMIUM mark" AND (for Daily/5d/MTD/YTD) today's LTD
has a value. Decided from the memoised `priced_value_book` frames, not by parsing the
aggregated reason strings. Hidden cards are named in ONE caption line; never a 0. Every
other scope's strip always shows every card. "Trading P&L T-1 = 0" with no trade dated
T-1 is the pre-existing convention, not a zeroed n/a.

**Real-browser recipe, extended.** For "does any callback error before its sub-tab is
opened", serve the scratch DB with `app.run(debug=True, use_reloader=False,
dev_tools_hot_reload=False, dev_tools_ui=True)`: dev tools are STRICTER than the
launcher's production server, so zero `.dash-fe-error__title` cards with
`suppress_callback_exceptions=True` is a real pass. The two console errors about
`dash-version.plotly.com ... CORS` are Dash's own dev-tools version check, not the app.
Tabs and sub-tabs open with a plain DOM `.click()` on the element whose text matches;
`dash_clientside.set_props('data-revision', {data: ...})` fakes a poll tick. See
[[real-browser-verification-2026-09-18]] for the Edge/DevTools setup and the
never-taskkill-by-name rule.

**Tests that open the live database.** tests/test_ui.py's three offenders
(`create_app()` / `create_app(db_path=None)`) now use tmp_path. STILL DOING IT, not in my
lane: tests/test_launch.py -- every `launch.main(...)` test reaches
`create_app(start_feed=True)` at ui/launch.py:163 with no path; proven by running it with
`RISK_DB` set to a scratch path, which then got created and schema'd. Fix: `monkeypatch.
setenv("RISK_DB", str(tmp_path / "risk.db"))` in its autouse fixture.

**Test-helper trap.** Walking a component tree with a stack (`pop()` / `extend()`) visits
children in REVERSE order; a dict of cards built that way has the wrong key order. Use a
recursive in-order walk when order is asserted.
