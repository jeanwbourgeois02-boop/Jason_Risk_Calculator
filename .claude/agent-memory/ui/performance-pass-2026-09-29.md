---
name: performance-pass-2026-09-29
description: Why the site lagged (Dash 4.4 redux dispatch per component x mounted components) and the five rules now in ui/ that keep it fast; measurement recipe
metadata:
  type: project
---

User 2026-09-29: "the site is laggy and slow - find out why and fix it". The lag was the browser:
in Dash 4.4.1 every component a callback renders dispatches one redux action that runs every
mounted component's selector, so cost ~ new components x components on the page (all six tabs
mounted: a data revision froze Chrome 63 s, Book Expand all 119 s). Now:

1. **One tab mounted** (`ui/app.py::build_tab_bodies(selected)`): the selected tab's layout, `[]`
   elsewhere; state survives through session persistence / session Stores. Never keep hidden tabs.
2. **One poll** (`revision.register(..., tick=)`, 15 s): `_poll` also sets the stale flag and the
   header's 17:00 roll. `_build_check` stays only for pages of older builds (input
   `LEGACY_POLL_ID`, in no layout). `_poll`'s outputs must never change (old pages ask by output).
   No per-tab safety Intervals (REFRESH_ID constants kept, unused).
3. **Memo per db revision** (`blotter_pricing.screen_memo`, single-flight): `shared_spreads(filled=)`
   (raw = engine default value_book via the raw store; filled = Book/header), `shared_curve`,
   Book/Exposure/P&L(base+period)/Risk gathers, `header.needed_marks`. Results are SHARED: never
   mutate. Risk's key adds config + research-db mtimes and a 15-min bucket (`outside_inputs_key`).
4. **Static rows** (`formatting.compact` on callback outputs; `static_runs` in the drawer): plain
   Tbody/Thead/Tr/Ul children become one `dcc.Markdown(dangerously_allow_html)` (react-jsx-parser,
   no redux), class `static-block` = `display: contents`. Rows with ids (Book names, chevrons, tab
   links) stay components. Escape `{}` in text (JSX). CSS: position selectors (`td:first-child`,
   `>` child) see the wrapper; overrides at the end of style.css.
5. sample.db syncs its Bloomberg library once when built (read-only readers recomputed it 18x).

**How to measure:** scratchpad `perf/`: `server.py <db> <port> <log>` (counts engine calls),
headless Chrome `--remote-debugging-port`, `cdp3.py <url> <out> <cport> all` (tabs + Book
interactions, redux dispatches), `cdp5.py` (data revision; waits 33 s for the 15 s poll),
`counts.py` (components per render, raw vs compact). Copy the db first; navigating to the same
URL with a hash does not reload (go via a different URL). Another chat may commit engine changes
mid-run: compare servers started after the same commit.
