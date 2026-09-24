---
name: estimated-dates-understate-risk
description: Contract-master's estimated last trade date (last weekday of the contract month) is weeks late for physical contracts; the lane alerts from the first business day of the month before instead (decided 2026-09-24)
metadata:
  type: project
---

Until Bloomberg's contract dates (`contract_static`) are on file, contract-master estimates the
last trade date as the last weekday of the contract month and gives no first notice date. Real
events come far earlier: CL month M stops trading about the 20th of M-1, COMEX GC/HG first notice
is the end of M-1, Brent (cash) the end of M-2. Before the fix, every estimated row on the
synthetic sample was GREEN at about 46 business days, while CLX26 really expires around 2026-10-20.

Decision (housekeeper took the lane's recommendation, 2026-09-24): while dates are ESTIMATED and
delivery is physical or unknown, the level is counted to `alert_date` = the first business day, on
the contract's own calendar, of the month before the contract month. Past that date but not past
the estimate is RED. Cash-settled and Bloomberg-dated rows are unchanged.

Contract-master was filling the blank `delivery` column in parallel (2026-09-24). Never hard-code
a root's delivery method in code or tests: pin it with a monkeypatch when a test needs blank.

**Why:** a paper trader must never hold a physical contract into delivery; the estimate is
"no later than", so alerts built on it fire late.
**How to apply:** keep the early alert unless the user reverses it; check `docs/open-questions.md`.
