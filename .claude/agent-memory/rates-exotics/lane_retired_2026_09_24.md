---
name: lane-retired-2026-09-24
description: engine/rates_vol and tests/test_rates_vol.py were deleted 2026-09-24 in the commodity conversion Phase 2; the rates-exotics lane is retired
metadata:
  type: project
---

The user approved on 2026-09-24 that swaptions, caps / floors, SABR and Bermudans leave the app with the rest of rates (commodity conversion, Phase 2, layer 3). `engine/rates_vol/` and `tests/test_rates_vol.py` were deleted (plain delete, not `git rm`, the index being shared). No trade source had ever carried these products.

**Why:** the app became Jason's commodity book; the rates-vol family had no ingest path and no consumer.

**How to apply:** if this lane is ever revived, the code is in git history before that date. The defensively created tables (`instrument_rate_options`, `rate_vols`, `rate_model_params`) were deliberately left on existing databases, unread and harmless. [[conventions]] describes the deleted code and is historical only.
