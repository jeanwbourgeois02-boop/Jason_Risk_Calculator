---
name: futures-upload-diagnostics-2026-09-16
description: reconciliation.py's futures-fill upload callback now renders ImportResult.messages as a warning panel; test invocation pattern for wrapped Dash callbacks
metadata:
  type: project
---

`data.ingest.xlsx_futures.load_futures_fills()` returns an `ImportResult` (int subclass)
with `.issues` / `.messages` (plain-English per-row diagnostics). `ui/tabs/reconciliation.py`'s
`_futures_confirm` callback now branches three ways: clean import keeps the old plain
"Imported N ..." string; partial import (some rows skipped) renders an
`html.Div(className="source-result--warning")` with a count line + `html.Ul` of the
messages; any exception is caught and replaced with a fixed plain-English string —
**never** interpolate `str(exc)` into the UI (that was the pre-2026-09-16 bug). New CSS
class `.source-result--warning` added in `ui/assets/style.css`.

**Test pattern for invoking a registered Dash callback directly** (used in
`tests/test_ui.py`): `app.callback_map[output_id]["callback"]` is wrapped by Dash's
`add_context` and requires internal kwargs (`outputs_list` etc.) that aren't worth faking
— call `getattr(cb, "__wrapped__", cb)` instead to get the raw function and invoke it
positionally with plain args (Input/State order). Confirmed present in this Dash version
(`dash` on Python 3.14 via `py -3`).

Also fixed in the same pass: `test_bbg_diagnostics_entry_point_falls_back_to_placeholder_when_real_module_absent`
renamed to `..._prefers_real_module_now_that_it_exists` once bbg-data landed
`data/bloomberg/bbg_diagnostics.py` (thin re-export of `tools.bbg_diagnostics.run_bloomberg_diagnostics`).
`ui/tabs/header.py::_bbg_diagnostics_entry_point()` picks it up automatically via its
existing lazy-import fallback — no header.py change was needed, only the test's
assertion.
