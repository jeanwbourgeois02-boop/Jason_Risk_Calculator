---
name: live-db-state-2026-09-16
description: what data/raw/risk.db and data/raw/sample.db contain on this dev PC (no terminal): risk.db empty since the 2026-09-28 macro-book backup; sample.db carries every product kind with synthetic marks but no curve_quotes / contract_static / Greeks
metadata:
  type: project
---

Dev-PC database content, re-checked 2026-09-28 (supersedes the 2026-09-16 snapshot, when
`marks` held 33 BNP_BVAL rows and 229 macro trades; those were purged / backed up to
`risk.db.backup-2026-09-28-henry-macro-book`):

- `data/raw/risk.db`: 0 trades, 0 marks, no `contract_static` table. Every coverage check
  gives its "nothing of that kind" pass row. Its status file
  (`risk.db.bloomberg_status.json`, 2026-09-18) records a pull that never connected, so
  every "last pull" check reads "not yet exercised".
- `data/raw/sample.db` (built by the in-app "View the sample book", 2026-09-28): 29 FUTURE,
  4 CMDTY_OPTION (one expired), 3 LME_FWD (one past prompt), 8 FX_FWD, 5 FX_OPTION, 1
  FX_SPOT. Marks 2025-12-31..today: BBG_BDH FUTURE_PX (options' own price included),
  BBG_BFXFORWARD SPOT / FWD_OUTRIGHT (LME pillars too), QL DELTA / PREMIUM for the FX
  options only. No GAMMA / THETA / VEGA, no `curve_quotes`, no `contract_static`, no LME
  BBG_INTERP prompt rows, no status file -- so it is the right DB to see every commodity
  warning row fire without a terminal.

**How to apply**: never read "fast" or "blank" on risk.db as saying anything about the
live app (CLAUDE.md guard rail). Use sample.db to exercise the panel's commodity rows; check
`SELECT source, mark_type, COUNT(*) FROM marks GROUP BY 1,2` before trusting this note again.
