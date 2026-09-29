---
name: shared-valuation-2026-09-29
description: book_positions takes value_fn / curve / spreads (Phase G, one valuation per date); only the commodities block values dates; how it was proved
metadata:
  type: project
---

`book_positions(conn, as_of, value_fn=None, curve=None, spreads=None)` since 2026-09-29 (Phase G, "one shared valuation per date"). Only `commodity_positions` values dates (through `curve_positions` -> `book_spreads`); the FX and FX-option blocks read legs and marks only. `curve=` is used as is; otherwise `spreads=` and `value_fn=` are handed to `curve_positions` (value_fn only if its signature has it, `inspect`).

**Why:** the Risk tab's Currency fold re-valued every period date through this path.

**How to apply:** proof recipe: build the sample with `ui.sample_book.build_sample_db(<fresh path>)` (a reused path can be locked by another process); its marks end 2026-09-18, so check there (at today every commodity is n/a and the comparison proves little). A counting wrapper showed book_spreads values 5 dates (as_of, prev bd, 5d, MTD, YTD refs), each once.
