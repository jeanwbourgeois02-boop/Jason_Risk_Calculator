---
name: macro-removal-2026-09-24
description: Phase 2 removal pass, 2026-09-24: equity pricer gone, sample-book tests now seed the synthetic sample's 5 FX options inline (why inline, not the upload path)
metadata:
  type: project
---

On 2026-09-24 (commodity conversion Phase 2, user yes), `pricer.price_equity_option` was deleted, the equity / commodity tests left `tests/test_options_pricing.py` (commodity coverage is listed-options-pricer's `tests/test_listed_options.py`), and FX option pricing stayed untouched (FX options are dormant but kept until the user decides).

The units-audit / expiry-day / expiry-week tests used to import `data/raw/new_sample_trades.csv` (the macro trader's real book) through `upload.import_blotter`. They now seed `SAMPLE_OPTIONS` inline: the 5 FX options of `data/sample/blotter_sample.csv`. Those are an EURUSD 1.18 call, a USDJPY 142.5 put, a USDJPY no-strike digital (152 put), and an EURUSD 1.15 put pair that was closed out on 2026-09-08. They are seeded in the shape `blotter._parse_option` writes.

**Why:** in that pass the ingest lanes were rewriting `blotter.py` mid-flight, and the upload path raised twice (the `irs_direction` import, then `FUTURE_SYMBOL_RE`). The removal ran top-down, so the ingest layer changes after the pricers. Seeding inline keeps this lane's tests on this lane's code.

**How to apply:** if ingest-parser changes the sample's options, update `SAMPLE_OPTIONS` by hand to match. The sample's dates are day-first and some are ambiguous (1/9/2026, 10/8/2026, 8/9/2026). I read them as D/M, which was not checked against the parser. Related: [[closed-out-options-not-priced-2026-09-22]] (options-pricer memory).

The portfolio FUTURE_PX gap noted here was closed the same day: see [[cmdty-options-portfolio-2026-09-24]].
