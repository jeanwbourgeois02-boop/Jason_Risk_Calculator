---
name: mark-checks-phase-g-2026-09-29
description: inventory.mark_checks (Data tab marks check, Phase G) - the four checks' exact rules, the choices taken on the brief's ambiguities, and the sanity rule's false-positive behaviour
metadata:
  type: project
---

`inventory.mark_checks(conn, as_of, today=None)` built 2026-09-29 for Screens redesign Phase G (Data: "can I trust today's numbers?"). Rows come from `mark_inventory` (LME_CURVE item dropped; the LME cash SPOT and prompt FWD are rows); trade ids and roles from `library.needed_on(include_unrequestable=True)` keyed like `_needed_marks` (SPOT settle = as_of).

Choices taken (low stakes, reported to the housekeeper):
- arrived = an official mark dated as_of; when not, value/mark_date show the last official before it (carried), status MISSING.
- fresh: past day = `backfill.is_close_row`; today = `live.book_today(stamp) == as_of` (a 17:00 NY close stamp maps to the NEXT book day, so is_close_row is also accepted).
- sane: >= 10 closes: 5 x median |daily change| of the key's last 60 closes, with a 0.5 % floor (my addition: a flat synthetic history has median 0); else 8 %. SPOT series = settle_date = as_of_date (a SPOT's key moves daily).
- usual_move_pct / sane_limit_pct (added 2026-09-29 for the ui's "usual move under 2 %" wording): usual = the absolute median / |previous close| (so max(5 x usual, floor) is exactly the absolute limit applied; the rule itself did not change). sane_reason: "moved +12.0 % in a day; its usual move is 1.0 %, the limit 4.9 %" ("since <day>" when the previous close is not yesterday; "only N closes on file, the limit 8.0 %" when short).
- units: only trades on the same instrument, role PAIR, products FUTURE / listed options / FX spot-fwd-swap / LME_FWD (never an FX option's premium vs spot). Listed options flag only the 100x band (50-200 or inverse): a premium can legitimately move 5x.
- blocks_what follows the near-marks order: a forward along the day's curve first, then an earlier close ("Daily uses yesterday's close"), then a later one, then other days' curves, else "no P&L" / "no USD P&L" (CONVERSION) / "no Greeks (P&L unaffected)" (UNDERLYING). Trade name = trades.strategy, trade id when blank (the synthetic sample has none).

**Why noted:** on a synthetic Gaussian walk the 5 x median rule with only ~15 closes flagged 3 of 45 normal moves (small-sample median runs low). Expect some CHECK noise until 60 closes are on file; the threshold is the user's brief, not mine to loosen.

Related: [[one-close-stamp-2026-09-28]]
