---
name: bnp-upload-summary-visible-2026-09-16
description: fixed a collaborator's regression that routed the BNP import summary to log.info only, on an app with no logging handler anywhere -- it was silently dropped
metadata:
  type: project
---

2026-09-16: code review found `ui/uploads.py`'s `_confirm` callback logging the
`import_report()` summary (new/identical/excluded/closed-forward-line counts) via
`log.info` instead of showing it, and the app had **no logging configured anywhere**
(no `basicConfig`, no handler) -- confirmed nothing was ever printed. Same dead-log-call
pattern exists in `data/ingest/bnp.py` (not touched, outside `ui/`; the fix there is just
that `logging.basicConfig` now exists so those calls work too).

Fix: `result = html.Div(message, className="source-result--info")` replaces the `""` on
success in `ui/uploads.py::_confirm`, styled like the futures-upload diagnostics pattern
in [[futures-upload-diagnostics-2026-09-16]] (one compact paragraph, not a per-row dump --
`import_report()`'s message is already one paragraph, not per-row, so it needed no
truncation). New CSS class `.source-result--info` in `ui/assets/style.css`.

Also added `logging.basicConfig(level=logging.INFO, ...)` to `ui/launch.py::main()` --
the real Dash entry point (`2_launcher.py cmd_start` just calls `ui.launch.main`), so this
stayed inside `ui/` ownership and never touched the housekeeper's `2_launcher.py`.

**Gotcha for testing multi-output Dash callbacks**: `app.callback_map` keys for
multi-output callbacks are `..out1.prop...out2.prop...` joined with a trailing hash, not
just the first output id (`"report-result.children"` alone raises `KeyError` even though
that IS one of the outputs) -- match by `next(k for k in app.callback_map if
k.startswith("..report-result.children...data-source-line"))` instead. Single-output
callbacks (like the futures one in [[futures-upload-diagnostics-2026-09-16]]) don't have
this problem, which is why that pattern's exact-key lookup worked there but not here.
