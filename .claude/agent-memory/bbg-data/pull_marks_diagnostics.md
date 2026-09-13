---
name: pull-marks-diagnostics
description: Diagnostics/probe layer in data/bloomberg/pull_marks.py and data/bloomberg/diagnose.py -- structure, exit codes, what counts as "partial", probe outcome, correlation ids
metadata:
  type: project
---

`data/bloomberg/pull_marks.py` writes a diagnostics JSON to `<out>.diag.json` alongside
every run (pull or `--probe`), always (try/finally in `main()`, including on an unhandled
exception, with the traceback embedded). `data/bloomberg/diagnose.py` (may import from the
repo, unlike pull_marks.py) reads that JSON and prints a plain-text report; exits non-zero
if the JSON records any (non-candidate) failure.

Superseded design note: an earlier version of this layer defined "partial" as "any stage
raised BloombergRequestError (TIMEOUT)". A reviewer found this let SECURITY_ERROR /
FIELD_EXCEPTION / NO_VALUE / rejected-interpolation rows silently vanish from the marks
CSV while still exiting 0. **Current design (C-A fix):**

- **"Partial" is defined purely by comparing key sets**, not by classification: after
  `run()` returns, `run_pull()` computes `requested_keys = {(instrument_id, settle_date,
  mark_type)}` from the request CSV and `written_keys` from the rows actually produced.
  `marks_csv_partial = requested_keys != written_keys`; exit code 5 whenever any key is
  missing, for ANY reason. This makes the exit code robust even if some future code path
  forgets to append a `failures` entry -- a fallback reconciliation loop in `run_pull()`
  synthesizes a generic `NO_VALUE` failure entry for any missing key not already
  explained by a more specific one.
- `build_spot_rows`, `build_future_rows`, `build_fwd_outright_rows` now all return
  `(rows, warnings, failures)` (3-tuple, was 2-tuple) -- `failures` is a list of dicts
  `{instrument_id, settle_date, mark_type, classification, detail}`, one per row that was
  NOT written. `run()`'s per-stage `except BloombergRequestError` blocks now also emit one
  such entry per requested row of that stage (via `_stage_timeout_failures`), plus the
  original synthetic `{"stage": ..., "error": ...}` entry (kept for the older tests/
  readability) -- `summary.failures` mixes both shapes; diagnose.py and callers should use
  `.get()` on whichever keys they need.
- `build_fwd_outright_rows` now catches `BloombergRequestError` **per row** (not just at
  the run()-level stage boundary), so a TIMEOUT on row 3 of 5 doesn't discard rows 1-2
  already resolved in the same call.
- New classification `CLASS_REJECTED = "REJECTED"`: used when our own logic rejects an
  interpolation (settle_date before spot or outside the tenor range) -- distinct from
  `CLASS_NO_VALUE` (Bloomberg had nothing) so diagnose.py / a human can tell "we asked for
  something Bloomberg can't answer" from "our own validation said no".
- `_classify_secs()` still classifies a whole batched request (e.g. all 8 tenor tickers)
  as one classification -- NOT per-ticker. This was a deliberate scope cut (see review
  item W-6): the functional goal ("a FIELD_EXCEPTION on one ticker shouldn't fail a row
  that didn't need it") is achieved by the key-set reconciliation above instead of a
  `_classify_secs` rewrite. Per-ticker detail is still visible in `raw_response`
  (fieldExceptions/securityError are recorded per security already).
- **Exit codes**: 0 OK (requested keys == written keys), 2 stale-`--as-of` refused, 3
  unhandled exception, 4 session/service open failure, 5 marks CSV partial (any missing
  key, for any reason). Probe mode always exits 0 once session+service open, regardless
  of individual step outcomes -- but see the outcome field below (C-B).
- **`Diagnostics.finalize_summary()` now takes an optional `outcome` param** overriding
  the exit-code-derived default ("OK"/"FAILED"). `run_probe()` passes
  `"PROBE_COMPLETE_WITH_FAILURES"` when any *non-candidate* probe step failed (candidate
  steps -- the `fwd_outright_direct_*` alternatives -- are expected to fail sometimes,
  that's the point of trying several field-name guesses, so they don't taint the outcome
  or `diagnose.has_failure()`). Session-open failure in probe mode passes
  `"PROBE_SESSION_FAILED"`. `diagnose.has_failure()` treats any `outcome not in (None,
  "OK")` as a failure, so "PROBE_COMPLETE_WITH_FAILURES" is correctly non-zero-exit in
  `diagnose.py` while `pull_marks.py --probe` itself still exits 0.
- **Correlation ids (W-1)**: every `fetch_reference`/`fetch_historical` call sends its
  request with a fresh `blpapi.CorrelationId` (`_CORRELATION_COUNTER`, itertools.count).
  Any received message whose `correlationIds()` don't include that id is a late/stale
  response from a previous (e.g. timed-out) request; it's discarded from `out`/`raw_secs`
  and recorded separately under that request's `late_responses` diag key -- and critically
  the loop does NOT break on such a foreign event even if its `eventType()` is RESPONSE
  (an `event_is_ours` flag tracks this), so it keeps waiting for the real response. Proven
  bug before this fix: a SPOT TIMEOUT followed by a FUTURE_PX request could pick up
  EURUSD's late response and treat it as FUTURE_PX's answer.
- **Environment block (W-2)**: `main()` now calls `diag.record_environment(host, port)`
  immediately after creating `Diagnostics()`, before `read_request_csv`/`check_not_stale`/
  `open_session` -- so the environment block is never `{}` on an early failure (stale
  refusal exit 2, missing request CSV, bad `--as-of`). `open_session()` now takes an
  optional `diag` param and updates `environment.session_started`/`service_opened` in two
  phases itself (session_started set as soon as `session.start()` succeeds, before
  `openService` is even attempted), so a diag distinguishes "session never started" from
  "session started but `//blp/refdata` wouldn't open" -- previously both looked identical
  (`False, False`).
- **`write_diagnostics()` (W-3, W-7)**: now `mkdir(parents=True, exist_ok=True)`s the
  output directory first, and wraps the whole write in try/except printing to stderr
  instead of raising (a write failure must never mask the run's real exit code). Also
  runs `_sanitize_for_json()` first, recursively turning NaN/±inf floats into the strings
  `"NaN"`/`"inf"`/`"-inf"` (bare `json.dump` writes invalid-JSON `NaN`/`Infinity` tokens).
- **Probe non-scalar detection (W-4)**: `run_probe()`'s `step()` helper now captures the
  return value of each `fetch_reference`/`fetch_historical` call and, on an OK
  classification, patches the diag request record with `scalar` (bool) and a richer
  `detail` (`repr` of the returned values) -- specifically it flags `scalar=False` when a
  `FWD_CURVE` field value isn't a plain int/float (bulk/array response). Previously
  `run_probe()` called `fetch_reference` directly, bypassing
  `fetch_fwd_outright_direct()`'s scalar check, so a bulk `FWD_CURVE` response looked like
  a bare "OK" with no evidence it was actually unusable.
- **`diagnose.py` open-questions evidence (W-6)**: the per-question evidence loop now
  prints `probe_name -> classification: detail` for every probe step that ran and got far
  enough to say something about the field/override (including FIELD_EXCEPTION/
  SECURITY_ERROR -- e.g. a `BAD_FLD` FIELD_EXCEPTION is itself strong evidence a field
  name is wrong, and must not be hidden). "no evidence" is now reserved for: the probe
  step was never run at all, or it never resolved (`TIMEOUT`/`SESSION_ERROR`,
  `classification is None`).
- **`diagnose.has_failure()` / candidates (W-5)**: excludes `candidate: True` records from
  the "hard failure" check (`_hard_failures()`), so exploratory candidate guesses failing
  is expected and doesn't make the whole diag look broken; also checks `summary.outcome`
  directly (any value other than `None`/`"OK"` counts, which folds in
  `PROBE_COMPLETE_WITH_FAILURES` for non-candidate probe failures and any pull-mode
  `"FAILED"`).
- **Open-questions title truncation**: `_read_open_questions()` now truncates each item's
  title at the closing `**` of its bold lead (regex `r"(\*\*.*?\*\*)"`), instead of
  printing the entire multi-hundred-char item text.
- Test fake `blpapi` in `tests/test_bloomberg.py::_install_fake_blpapi` gained
  `CorrelationId` (equality/hash by wrapped value) and `MultiEvent` (wraps a list of event
  specs so one `sendRequest()` can queue several `nextEvent()` results -- used to script a
  late/stale response with an old correlation id arriving before the real one). A
  responder callback may now take either `(request)` (old signature, still supported) or
  `(request, correlation_id)` -- detected via `inspect.signature` arity at install time.
  `FakeMsg` gained `correlationIds()`.
- `open_session()` signature changed: now `(host="localhost", port=8194, diag=None)` --
  backward compatible (diag optional, existing 1- and 2-positional-arg call sites still
  work).
- `pull_marks.run()` still returns `(rows, warnings, failures)`, unchanged arity, but
  `build_spot_rows`/`build_future_rows`/`build_fwd_outright_rows` changed from 2-tuple to
  3-tuple returns -- if you call these directly (not just through `run()`), update the
  unpacking.

## Round-2 review fixes (W-1..W-3, S-1..S-4)

- **`diagnose.has_failure()` is now mode-dependent (W-1)**: pull mode follows
  `summary.outcome` / `summary.marks_csv_partial` / `summary.failures` / `exception`
  ONLY -- a request-level classification (e.g. one tenor ticker of eight in a batched
  `fetch_tenor_points()` call coming back `FIELD_EXCEPTION`, per `_classify_secs`'
  whole-batch classification) no longer flips the exit code by itself when every
  requested key still got written (interpolation only needed the other, successful
  tenors). Probe mode keeps the old behaviour: any non-candidate hard failure among
  `diag["requests"]` still counts, since probe has no requested-vs-written
  reconciliation to fall back on. Gate is `summary.mode` (`finalize_summary()` always
  sets it, defaulting to `"pull"`), so a diag missing that key is treated as pull mode.
  The old "belt-and-suspenders" `_hard_failures()` check that ran unconditionally is what
  caused the false positive -- removed for pull mode, kept for probe.
- **`build_fwd_outright_rows` propagates real classifications (W-2)**: the "no SPOT to
  interpolate off of" and "no FWD_POINTS_SCALE returned" failures previously always
  hard-coded `CLASS_NO_VALUE`, hiding e.g. a SECURITY_ERROR/TIMEOUT on the underlying SPOT
  or scale request. Now `run()` captures `_batch_classification(diag)` immediately after
  `build_spot_rows()` returns (before later stages overwrite `diag.requests[-1]`) and
  passes it into `build_fwd_outright_rows(..., spot_batch_classification=, spot_batch_detail=)`.
  For the scale request, classification is captured right after `fetch_tenor_points()`
  returns (its own last call is the `FWD_POINTS_SCALE` lookup) and cached alongside
  `(points, scale)` per pair in `tenor_cache` (now a 4-tuple).
- **`write_marks_csv` mkdirs its output directory (S-1)**: `Path(path).parent.mkdir(parents=True, exist_ok=True)`
  before opening the file -- previously a missing parent directory raised
  `FileNotFoundError`, caught by `main()`'s broad `except Exception`, turning a successful
  Bloomberg pull into exit 3 with the rows silently discarded (they only ever lived in
  memory). `write_diagnostics()` already did this for the diag JSON; now both do.
- **Bad `--as-of` is an argument error, exit 2 (S-2)**: `main()` now validates
  `date.fromisoformat(args.as_of)` itself, right after `diag.record_environment()` and
  before calling `run_pull()`/`run_probe()` (both of which also call
  `date.fromisoformat(args.as_of)` -- previously a malformed date's `ValueError` surfaced
  several calls deep and was caught by the generic unhandled-exception handler, exit 3).
  A diag JSON is still written on this path (`summary.exit_code == 2`,
  `failures: [{"stage": "as_of_validation", ...}]`) -- exit code 2 is shared with the
  pre-existing stale-`--as-of`-refusal case (`check_not_stale`); both are "problems with
  `--as-of`". Module docstring's exit-code table updated to say "bad `--as-of` (invalid
  ISO date, or stale/refused)" instead of just "stale".
- **Probe non-scalar check no longer keyed on the field name `FWD_CURVE` alone (S-3)**:
  `run_probe()`'s `step()` helper now flags `scalar=False` for ANY value of a **candidate**
  step (the closure's `candidate` bool), not just when the field happens to be named
  `FWD_CURVE` -- so `fwd_outright_direct_alt_fwd_outright_field` (field
  `FWD_OUTRIGHT_PRICE`) gets the same bulk-response protection as the `FWD_CURVE`
  candidates. Non-candidate steps (`tenor_1m`/`tenor_3m`/`fwd_points_scale`/spot probes)
  are intentionally excluded from the generic check (only `FWD_CURVE`/`FWD_OUTRIGHT_PRICE`
  by name still apply there defensively) so a legitimately non-numeric scalar field like
  `SETTLE_DT` (a date string) on `tenor_1m`/`tenor_3m` isn't misflagged as non-scalar/bulk.
- **Test fake `HistoricalDataRequest` responders now answer every requested security
  (S-4)**: every responder in `tests/test_bloomberg.py` that handles
  `HistoricalDataRequest` used to build a response only for `request.securities[0]`,
  which happened to work because every existing test only ever batches one ticker per
  `HistoricalDataRequest` call. Fixed by looping over `request.securities` and returning
  one `securityData` dict per ticker (the fake's `sendRequest()` already bundles a list of
  dicts into one event with multiple messages -- `fetch_historical`'s message loop already
  handles that correctly, this was purely a test-fixture gap). The correlation-id/late-
  response test (`test_pull_marks_late_response_after_timeout_is_discarded_and_recorded`)
  was deliberately left as single-ticker-per-call (it's testing cross-request correlation
  routing, not batching), as was the isolated `fetch_fwd_outright_direct` field-exception
  unit test (always sends exactly one ticker by construction).
- Exit-code table and probe step names are unchanged by this round: still 0 OK / 2 bad
  `--as-of` / 3 unhandled exception / 4 session/service open failure / 5 marks CSV
  partial, and the fixed probe sequence (`session_start`, `spot_reference`,
  `spot_historical`, `fwd_outright_direct_primary`, `fwd_outright_direct_alt_reference_date`,
  `fwd_outright_direct_alt_fwd_outright_field`, `tenor_1m`, `tenor_3m`, `fwd_points_scale`,
  `es_settle_px_settle`, `es_settle_px_last`) is untouched.
