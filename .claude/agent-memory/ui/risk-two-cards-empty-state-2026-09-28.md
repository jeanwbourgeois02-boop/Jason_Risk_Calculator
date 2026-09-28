---
name: risk-two-cards-empty-state-2026-09-28
description: Risk tab down to two cards (VaR, blended vol vs target), the worst day a line inside the commodity scenario fold, engine reasons in plain words through risk.plain_reason; the Book's "No blotter loaded" card shared by Exposure, P&L and Risk with one pattern idx per tab; a parallel ui agent was editing book.py / formatting.py at the same time
metadata:
  type: project
---

What is true after the 2026-09-28 labelling / empty-state wave (user decisions relayed by the session):

- **Risk cards**: two, `1-day VaR (95 %, last year)` (the confidence from `config.var_confidence`)
  and `Blended vol vs target` (the target in the note), `className="cards cards--two"`
  (`.cards--two` in `ui/assets/style.css`: two columns of at most 400 px, never stretched).
  The "Worst day ex shocks" card, its "% of the cap" and the "over cap" flag are gone.
- **Worst day**: `risk.worst_day_line` (id `risk-worst-day`), the FIRST child of the
  "Commodity scenario stress" fold in every branch (computed, none, not computed): the RAW
  figure `book.worst_1d_raw_usd` with `worst_1d_raw_date`, the reach in words ("prices Sep 2020
  to Sep 2026", the book series' first/last date, else the commodity history's), "a historical
  replay, not a scenario". The ex-shocks figure lives on that figure's hover only. A dash with its
  plain reason when NaN.
- **Plain reasons**: `risk.plain_reason` maps "; "-segments by prefix (`_PLAIN_REASONS`): "no
  market history…" -> "no FX price history on this PC", "no commodity history…" -> "no commodity
  price history" (except "no commodity history on or before <date>", kept), "no open commodity
  futures…" -> "no positions"; a "tried C:\..." tail is cut; unknown text passes through. Applied
  in `_why`, the table records/hovers, `exposure_words`, the folded-FX line, the drawer lines and
  the scenario details. The folders tried appear only in the Notes drawer (`history_line`);
  the caption's hover is `history_words` (no path).
- **Empty state**: `book.trades_on_file(conn)` (COUNT of `trades`, whatever the date) is the
  test; `book.empty_state(data=None, idx="book")` takes the tab's idx so the Upload button
  (`{"type": "book-empty-upload", "idx": idx}`) and the sample link (`sample_book.view_link(idx)`)
  never repeat an id: every tab body is always in the DOM. Exposure (`curve.gather` puts
  `n_trades`, `body` checks `== 0`), P&L (`pnl.gather` adds `n_trades`) and Risk (`render`,
  before `book_risk`) render it. `n_total` (the Book's own trigger) counts trades valued on the
  as-of, so the Book alone still shows the card for a date before the first trade (unchanged).
- **Tests** that stub `ui.app` and call `risk.render` must monkeypatch `risk.trades_on_file`
  (else an empty scratch db gives the card); a stub needs `active_db_path` for the sample link.
- **Parallel edits**: while this wave ran, another ui agent was rewriting `ui/tabs/book.py`
  (group-by Commodity | Strategy | Type | Instrument) and `ui/tabs/formatting.py` (trade types).
  Two agents on one file at once is a real hazard: verify one's own edits survive
  (`grep`) and never overwrite the other's; report it in the Handoff.
