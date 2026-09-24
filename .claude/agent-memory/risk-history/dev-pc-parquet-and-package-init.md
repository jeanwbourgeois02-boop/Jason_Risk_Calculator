---
name: dev-pc-parquet-and-package-init
description: Dev PC has no pyarrow or fastparquet, and engine/risk/__init__ imports metrics.py, so history tests break when risk-metrics' import lags a history.py change
metadata:
  type: project
---

On the dev PC (checked 2026-09-24) neither pyarrow nor fastparquet is installed, so no parquet read in `engine/risk/history.py` can be exercised here; `tests/test_risk_history.py` tests only what needs no parquet engine (file list, status shape, missing-folder / missing-file / unreadable-file reasons, cache key, folder order).

`engine/risk/__init__.py` does `from .metrics import book_risk`, so importing `engine.risk.history` runs `metrics.py`. When a history.py name is removed before risk-metrics drops its import (Phase 2, `RATES_FILE`), my own tests fail at collection. Verified them by stubbing `sys.modules['engine.risk']` as a bare package with `__path__` in a `py -3 -c` wrapper around `pytest.main`.

**Why:** lanes edit in parallel; my removal and the consumer's import removal land in the same wave.
**How to apply:** when removing an exported name, list the consumer's import line under Requests, and verify my tests with the stub if the consumer has not landed yet. The Phase 4 `commodity_history.py` will read SQLite (the research app's `price_daily`), which avoids the parquet gap altogether.
