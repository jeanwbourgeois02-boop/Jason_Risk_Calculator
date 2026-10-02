---
name: book-structures-2026-10-02
description: CURRENT Book (2026-10-02): 11 cols Trade|Size|Entry|Now|Ratio x2|Usual|Z x2|P&L x2, one row per spread found from the fills (struct_blocks), box arbs, leg USD-per-unit beside, closed spreads in the fold; event chips name the contract; marks chip "N of M updated today"; proof recipe
metadata:
  type: project
---

User 2026-10-02 ("i dont know if the lots are the same / ... unified units for entry price / which of the
three spread types / what if there are multiple spreads?"), built on spreads-engine's structures (a4c4cd7).

- Book columns: Spread at entry / Spread now left; Entry / Now carry a spread's level (quote.entry/now, USD per
  quote.unit, max(2, tick) dp) or a leg's average cost (its own unit + grey "· 4,008.8 $/t" from quote.legs
  usd_entry/usd_now). Size = size_words(quote.size, qty_unit) + grey lots; leg Size + grey physical qty.
- `struct_mode(t)`: '' (closed trade, pseudo, no sub_spreads -> legacy trade_blocks), 'single' (one open spread,
  nothing else: on the trade row), 'multi'. `struct_parts`: open / unmatched / closed / outside (outside_structures).
  Fills are disjoint per spread: spread P&L = fill_sum(fill_ids), legs = fill_sum(leg trade_ids) (shared flag
  dropped), so spreads + unmatched + outside = trade to the cent (scratch sums.py: 0 mismatches).
- Type tag (tk-type-tag) on every spread; tf.TYPE_WORDS CALENDAR now "Term structure", + TERM_STRUCTURE, BOX.
- Spread flags = struct_chip; the trade chip drops Hedge wrong way/too big and sides_off when spreads carry them;
  totals' "N to act on" adds struct_flag_items.
- Closed spreads of open trades go in the Closed fold (closed_struct_blocks), fold P&L includes their fill_ids.
- event_sentence: "SHFE Zinc Oct26 lots to a multiple of 5 by 30 Sep, passed" (no colon: ui-check PART rule);
  estimated after "≈", no count. book_contracts._next passes event_label etc. through.
- struct_zone: risk's spread zone kept only if its level_legs are the spread's own (the $/st-on-iron-ore guard).
- header.marks_updated reads shared_trade_book legs: INTERP "marks? of <as_of>" = the day's own curve = updated;
  other INTERP / mark_as_of < as_of / no mark = not updated; prev close INTERP (not on prev day's curve) counted.
  Cannot see a holiday exchange's stale live price stamped today (Request to bbg-live).

**How to apply:** proof scratch `ui/` (session scratchpad): setup.py (real export + spec 10.1 marks), render.py
<db> <as_of> [1 hovers] [trade], sums.py, chip.py, chk.py (ui-check rules on any db, .venv python), shot.py
(take_shots on any db with uc.AS_OF patched; .venv python has playwright, py -3 does not).
Supersedes [[book-two-legs-ratio-table-2026-10-01]].
