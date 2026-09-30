---
name: dev-pc-parquet-and-package-init
description: engine/risk/__init__ imports metrics.py, so a removed history.py name breaks collection; parquet is gone from history.py since 2026-09-30
metadata:
  type: project
---

Since 2026-09-30 `engine/risk/history.py` reads no parquet at all (the nm-dashboard folder reader was replaced by the book database's `price_history` FX rows), so the dev PC's missing pyarrow no longer matters to my files; the `pyarrow>=15` line in `2_launcher.py` (infra) exists only for that retired reader.

`engine/risk/__init__.py` does `from .metrics import book_risk`, so importing `engine.risk.history` runs `metrics.py`. When a history.py name is removed before risk-metrics drops its import, my own tests fail at collection. metrics.py imports `YIELDS_FILE, History, load_history` from history.py: keep those three names (kept on 2026-09-30).

**Why:** lanes edit in parallel; my removal and the consumer's import removal land in the same wave.
**How to apply:** when removing an exported name, list the consumer's import line under Requests, and verify with a direct call if the consumer has not landed yet. See [[price-history-reader]].
