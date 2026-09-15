---
name: quantlib-install-status
description: QuantLib is already installed (1.43) in this repo's Python environment; no install step was needed
metadata:
  type: project
---

`py -3 -c "import QuantLib; print(QuantLib.__version__)"` returned `1.43` without any
install step on 2026-09-15 -- QuantLib was already present in this environment's Python
(`C:\Users\jeanw\AppData\Local\Programs\Python\Python314`). `pip install QuantLib` was
never attempted/needed.

**Why this matters**: `requirements.txt` now pins `QuantLib==1.43` (added at the repo
root, the one narrow exception to `engine/rates/`'s directory ownership authorized for
this task). `tests/test_rates_pricing.py` still gates every QuantLib-dependent test
behind `@needs_quantlib` (`skipif(not HAVE_QUANTLIB)`), matching the blpapi-skip pattern
used elsewhere in this repo (`tests/test_bloomberg.py`), so the suite stays green on a
machine where QuantLib is absent -- but as of this writing it is present, so all 15
tests in that file actually ran (not skipped).

**How to apply**: don't assume QuantLib needs installing before running
`engine/rates/` tests in this environment. If a future session hits `ImportError:
QuantLib`, that's a real environment regression, not the expected state.
