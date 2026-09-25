---
name: display-helpers-2026-09-25
description: Shared screen helpers of the screens redesign (short_money, about, marker, issues_drawer, amount_short/whole_units) and why a DataTable prints k / M / G, not k / m / bn
metadata:
  type: project
---

Screens redesign Phase A (user approved 2026-09-25): ui-shell gave every tab lane shared
display helpers. HTML side (`ui/tabs/formatting.py`): `short_money` ("51.0k", "1.65m",
"2.40bn", real minus U+2212 or parens), `about` (title with definitions on hover, info
mark), `marker` (short text, sentence in `title`), `issues_drawer` ("Data issues (N)"
Details, None when empty). DataTable side (`ui/tabs/ranking.py`): `amount_short` (d3
`(.3~s`) + the mandatory companion `whole_units(records, cols)`.

**Why the table letters differ:** Dash DataTable formats with stock d3-format; SI letters
are fixed (k, M, G, and m = milli), and neither the locale nor a fixed prefix can change
them. Lowercase m / bn in a table is only possible by pre-formatting strings, which breaks
numeric ranking (user rule 2026-09-22 "for all tables, make sure we can rank"). So tables
read 1.65M while HTML reads 1.65m. And any fraction under 1 prints as milli ("400m") unless
rounded first, hence `whole_units`.

**How to apply:** if the user objects to "M"/"G" in tables, the fix is a per-column scale
(values divided, a static "m" suffix), not a string column. Keep `si_text` in step with d3
if `amount_short`'s specifier changes (it sizes columns in `display_length`).

Gotcha seen the same day: a `−` escape typed inside a Bash heredoc reached the file as
the literal character (even inside a Python raw string); build such escapes with
`chr(92) + "u2212"` or the Edit tool, and check with `file` that the .py stays ASCII.
