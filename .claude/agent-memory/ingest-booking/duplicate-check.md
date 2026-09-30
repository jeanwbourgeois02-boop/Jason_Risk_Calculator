---
name: duplicate-check
description: 2026-09-30 possible duplicates (same fill under two Trade Ids) are flagged at call time, never dropped; upload_not_loaded keeps the last file's excluded / cancelling rows; last_upload_outcome is the Data tab's one reader
metadata:
  type: project
---

User 2026-09-30 on the Data tab: "what i need is check of what is and isnt pulled from the blotter
and why - and obviously no duplicates when updating with new blotter".

- The merge by Trade Id already stops a Trade Id loading twice. What it cannot see is a re-booked /
  re-exported fill under a NEW Trade Id. `possible_duplicates(conn)` flags those, read at call time
  (no stored copy), so a cancel or re-upload clears a group at once. Flag only (hard rule 6).
- Match key: trade_date, instrument_id, last leg settle_date (so LME prompts / FX value dates never
  match), side, |qty|, stored price. Chosen over the raw Price cell / broker symbol on purpose: the
  stored price is the cell x broker scale (same cell -> same fill) and it also covers rows loaded
  before broker_symbol / broker_price existed.
- Kind from `upload_history`: no single upload carried all members -> different_uploads (likely a
  re-booking); one file carried them all -> same_file ("may be two real fills"); a member with no
  history -> unknown. Jason's export is cumulative, so a re-booked fill whose old id is still in
  the export reads as same_file: that is the broker's own view, correct.
- `check_file` computes the same groups on the book as the merge would leave it, the file counted
  as one more upload labelled "in this file"; verified equal to the real merge's result.
- `upload_not_loaded` (not `upload_issues`): ui/tabs/blotter_fills.py counts every upload_issues
  kind except WARNING / UNRECOGNISED as a reject, so new kinds there would inflate its count.
- Rows are classified by reusing `parse_check._report` on the load's own ParseResult (no second
  parse). parse_check.py is not in CLAUDE.md's lane table; this lane edits it (it wraps upload's
  reader and merge rule) and asked the housekeeper to list it here.

**Why:** Jason re-uploads a cumulative broker export; a silent double-booked fill doubles P&L.
**How to apply:** never auto-drop or merge a group; keep the dry run and the real reader on the one
`duplicate_groups` function. Related: [[upload-merge-rule]], [[upload-history]], [[every-row-loads]].
