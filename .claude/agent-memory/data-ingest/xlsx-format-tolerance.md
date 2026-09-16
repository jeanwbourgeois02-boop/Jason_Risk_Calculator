---
name: xlsx-format-tolerance
description: xlsx_futures.py loosened for cosmetic format variation + structured row diagnostics; ui-shell still needs to consume ImportResult.issues
metadata:
  type: project
---

`data/ingest/xlsx_futures.py` (2026-09-16) now tolerates cosmetic-only variation in the
workbook's "All FX trades" sheet: sheet name case/whitespace, header cell whitespace/case,
header column reorder (matched by alias against `date`/`pair`/`quantity`/`tenor`/`fill`
names, falling back to the documented A-E position only when a header is blank/unmatched —
needed because the reference workbook itself leaves the pair column B header blank), a
blank leading row before the header, and date values given as Excel date, ISO string, US
mm/dd/yyyy string, or Excel serial number.

Genuinely bad data (unparseable date, non-numeric fill, unrecognised Quantity formula,
pair not matching `<ROOT><month code><digit> Index`) is never guessed at: that single row
is skipped and recorded as a `RowIssue(row, column, issue, raw_value)` with a plain-English
`.message`, while the rest of the file still loads. `read_futures_fills()` returns a
`FillList` (list subclass with `.issues`); `load_futures_fills()` returns an `ImportResult`
(int subclass with `.issues`), so both stay backward-compatible with code that treats them
as a plain list / int.

**Follow-up owed to ui-shell** (reported, not done — out of data-ingest's directory):
`ui/tabs/reconciliation.py`'s `_futures_confirm` callback currently only interpolates
`inserted` into a plain success string and shows the raw exception `str(exc)` on failure.
It should read `result.issues` / `result.messages` to render a proper upload-summary panel
("N fills loaded, M rows skipped: <reasons>") instead of an all-or-nothing message, per the
2026-09-16 user request that xlsx uploads never fail opaquely.
