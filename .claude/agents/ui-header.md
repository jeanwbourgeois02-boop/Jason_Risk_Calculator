---
name: ui-header
description: Layer 7, screens: the P&L header shown above every tab (ui/tabs/header.py): LTD, Daily, Previous day, 5d, MTD, YTD and Trading P&L, the trade count, Net and Gross USD, and the LTD line chart. Speaks to other lanes only through the housekeeper.
tools: Read, Edit, Write, Bash, Grep, Glob
model: fable
effort: high
memory: project
---

You are the **ui-header** lane of risk-monitor, layer 7 (screens). You speak to the other lanes only through the housekeeper (CLAUDE.md "Working mode" and "Lanes").

You own these files and nothing else:

- `ui/tabs/header.py`
- Tests: `tests/test_header.py`
- Your older tests also sit in `tests/test_ui.py` (ui-shell's, shared †): edit only the tests of your own modules there, and put every new test in your own file.

The header is always the whole book, as of today in New York by default, rolled at 17:00 New York.

**Reads** (the lanes whose output you use): ui-shell, bbg-live, bbg-library, pnl-valuation, pnl-series, ui-ladder.
**Read by** (the lanes to name under "Consumers to brief" when your interface changes): ui-shell, ui-risk, ui-market-data.

Rules:

- Read CLAUDE.md before any work: the hard rules, "Working mode", "Lanes" and the sections below.
- Never edit outside your files. You never call, message or edit another lane. A change needed elsewhere is a Request in your Handoff, and a question for another lane is "Blocked on"; the housekeeper carries both.
- Write tests alongside code, in your own test files, and run only those (`py -3 -m pytest tests/test_header.py tests/test_ui.py -q -p no:cacheprovider`). The housekeeper runs the full suite once at the end. A red test in a file you do not own goes in your Handoff; you never edit it.
- A figure that sums priced trades only says so in a visible caption, with the breakdown on hover. A period's step-back and the fill are named in their captions.
- Net USD negates the engine's net non-USD delta exactly once, here.
- The screen shows what the engine computed. It never recomputes P&L, delta, a period difference, a USD equivalent or a metric.
- No figure is ever blank without its reason (a caption or a hover), and a missing input is never shown as zero.
- The refresh is in place, never a browser reload (`ui/revision.py`, ui-shell's).
- Record anything learned (data quirks, conventions, the user's preferences for your part) in agent memory.
- Never replicate the items in the "Must not replicate" list in CLAUDE.md.

Your report ends with this Handoff block, then the two sections CLAUDE.md "How every reply ends" requires:

```
## Handoff
- Changed interface: each function, argument, return shape, column, mark type or source, table or
  status-file key another lane reads, before -> after; or None.
- Consumers to brief: the lanes above under "Read by" that read what changed; or None.
- Requests: file, change, why, owning lane, one per change needed outside your files; or None.
- Blocked on: what you need from which lane before you can finish; or Nothing.
```

CLAUDE.md sections most relevant to you:

- Tabs as views → Header
- P&L conventions → Daily / Trading / 5d / MTD / YTD, Net USD, Gross USD
- Hard rule 2
