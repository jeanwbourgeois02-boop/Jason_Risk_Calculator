---
name: lane-retired-2026-09-24
description: ui-rates lane retired 2026-09-24 (Phase 2 removal): ui/tabs/rates.py and tests/test_ui_rates.py deleted with git rm; the other notes here describe a module that no longer exists
metadata:
  type: project
---

The user approved on 2026-09-24 that rates / IRS leave the app (CLAUDE.md "Commodity conversion plan", Phase 2). ui-rates deleted `ui/tabs/rates.py` and `tests/test_ui_rates.py` (staged with `git rm`, not committed); the Blotter had already stopped wiring the Rates sub-tab and the shell had stopped expecting its ids.

**Why:** the book is now Jason's commodity book; no IRS product is shown.

**How to apply:** the other notes in this folder ([[rates-tab-2026-09-15]], [[rates-direction-dropdown-2026-09-18]]) are history only. The general UI lessons in them (DataTable loading state wiping typing, style.css greying inline borders, real-browser click-testing via Edge DevTools) may still help other UI lanes, but no rates module exists to apply them to.
