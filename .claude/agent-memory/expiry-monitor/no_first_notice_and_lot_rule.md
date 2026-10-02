---
name: no-first-notice-and-lot-rule
description: 2026-10-02 user: SHFE/INE/DCE/ZCE/GFEX/OSE have no first notice day (key on last trade even if Bloomberg stores FUT_NOTICE_FIRST); SHFE CU/AL/ZN/PB 5-lot delivery-month rule is a row event
metadata:
  type: project
---

User approved 2026-10-02 after the Book's chip read "First notice in 6 days" on Jason's SHFE zinc
(no contract, no date, and SHFE has no first notice day). Bloomberg does store a
FUT_NOTICE_FIRST for SHFE contracts, so the old "physical and fnd <= ltd" rule picked it.

- `levels.NO_FIRST_NOTICE_EXCHANGES` (SHFE, INE, DCE, ZCE, GFEX, OSE): keyed on the last trade
  date whatever Bloomberg stores; reason says so. Euronext, GME, SGX rubber, BMD and the ICE
  energy roots were left out on purpose (not confident; ignoring a real FND alerts too late).
- `levels.DELIVERY_LOT_MULTIPLES` (SHFE:CU/AL/ZN/PB = 5): a position not in whole multiples must
  be trimmed by the last business day of month M-1. It becomes the row's event (`LOT_MULTIPLE`,
  an exact date, `event_estimated` False) while its deadline precedes the alert date, or once it
  is past with the last trade still ahead (RED: a breach). Jason's real book had -627 ZN Oct26
  on 2026-10-02: RED by this rule (coordinator 2026-10-02: a missed deadline is a breach to fix, RED, never EXPIRED).
- Every row now carries `event_label`, `event_estimated`, `event_business_days`,
  `first_notice_applies`, `delivery_lot_*` (the ui names the contract from `contract_id`).

**Why:** the screens must name the contract, the event and the date; a paper trader must never
hold into delivery, and a wrong "first notice" teaches the wrong rule.
**How to apply:** the golden book pins these rows, so it needs the user's yes to re-pin after this
change. See [[estimated-dates-understate-risk]], [[phase5-options-lme-rows]].
