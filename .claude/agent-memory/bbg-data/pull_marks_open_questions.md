---
name: pull-marks-open-questions
description: Unverified Bloomberg field/override choices in data/bloomberg/pull_marks.py that need checking on the real terminal
metadata:
  type: project
---

`data/bloomberg/pull_marks.py` is a standalone script for the Bloomberg machine (no repo
imports; blpapi/pandas/stdlib only). Field and override choices made there are
**guesses**, flagged UNVERIFIED in the module docstring, and should be confirmed by
whoever has terminal access before relying on them:

- SPOT: `HistoricalDataRequest` field `PX_LAST` for the single as-of date (changed from an
  earlier `ReferenceDataRequest` PX_LAST design: a live ReferenceDataRequest pull cannot
  be correctly labelled with a past as_of/snapped_at if the script runs after that date,
  e.g. the next morning — HistoricalDataRequest for the exact as_of date avoids that,
  matching the FUTURE_PX approach). Still unconfirmed whether PX_LAST (vs PX_MID) is the
  desk's actual spot convention.
- FUTURE_PX: `HistoricalDataRequest` field `PX_SETTLE` for the single as-of date. Chosen
  because CLAUDE.md's BNP FUTURES row says "Price = settlement price".
- FWD_OUTRIGHT direct (broken/non-standard settle date): `ReferenceDataRequest` field
  `FWD_CURVE` with overrides `FWD_CURVE_QUOTE_FORMAT='OUTRIGHTS'` and
  `SETTLE_DT=<YYYYMMDD>`. **Genuinely unverified** — this is the most likely candidate
  field/override combo for a single broken-date FX forward outright but has not been
  checked live. On a real terminal `FWD_CURVE` can come back as a bulk field (list of
  rows) rather than a scalar when the override doesn't fully pin it down; the code
  treats any non-`(int, float)` response as "no direct value" and falls back to tenor
  interpolation (logged as a warning) instead of crashing on `float(list)`.
- FWD_OUTRIGHT fallback (tenor interpolation): standard tenor tickers
  `'<PAIR><TENOR> Curncy'` for tenors `SP, 1W, 2W, 1M, 2M, 3M, 6M, 1Y` — **ON and TN are
  deliberately excluded**: their settle dates fall before spot and their points use the
  pre-spot quoting convention (e.g. outright = spot − points for TN), which this
  module's `outright = spot + points/scale` would apply backwards. Fields `PX_LAST`
  (forward points) and `SETTLE_DT` (that tenor's settlement date), plus
  `FWD_POINTS_SCALE` on `'<PAIR> Curncy'` for the points divisor (not hard-coded per
  pair — pulled live since it varies, e.g. 100 vs 10000). Outright = spot + points/scale.
  Interpolation is linear in **forward points**, not outright levels, between the two
  bracketing tenor dates. Settle dates before SP or after the last tenor are
  rejected/logged, never extrapolated.
- Staleness guard: since FWD_OUTRIGHT is unavoidably a live pull, `pull_marks.py` refuses
  to run when `--as-of` is not today (America/New_York) and the request has any
  FWD_OUTRIGHT rows, unless `--allow-stale-as-of` is passed (in which case it logs a
  warning and proceeds). See `check_not_stale()`.
- `BBG_INTERP` is a new `marks.source` value (added for this fallback path) and is
  **never official** — `marks_official` / `OFFICIAL_MARK_SOURCE` in
  `data/ingest/schema.py` is unchanged, so it never overrides `BBG_BFXFORWARD`.

The network layer (`fetch_reference`, `fetch_historical`) is a thin, plain-dict-in/out
wrapper so it can be exercised in tests via a fake `blpapi` module injected into
`sys.modules['blpapi']` before import — see `tests/test_bloomberg.py::_install_fake_blpapi`
for the exact narrow surface it needs to implement (SessionOptions, Session with
start/openService/getService/createRequest/sendRequest/nextEvent/stop, Event.RESPONSE).
