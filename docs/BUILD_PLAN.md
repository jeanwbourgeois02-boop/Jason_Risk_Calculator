# Build plan: one valuation, one line, four tabs

Authorised by the user on 2026-09-15. Supersedes the 2026-09-14 "literal workbook
arithmetic" correction as the app's headline calculation. The workbook copy is kept,
unchanged, as a reconciliation view only.

Scope now: FX spot, forwards, swaps, futures, cash. IRS and options come later and
must fit the same structure without changing it.

## 1. The model

One list, one number, everything else is a view.

- **Blotter** = every trade ever done, open or settled, one row each with its P&L.
- **LTD** = sum of every row. Continuous through time. It moves only when marks move
  or a trade is added; settlement does not move it because the settled row is frozen
  at the same value it had.
- **Period P&L** = LTD now minus LTD at a stored close. Daily, 5d, MTD, YTD are the
  same subtraction with different reference dates. Nothing is ever summed day by day.
- **Delta** = open legs summed by currency at spot. The ladder. No P&L on it.

Three layers:

| Layer | Holds | Rebuildable? |
|---|---|---|
| 1 Stored | trades, legs, marks per close, realised results, calendar | no, this is the memory |
| 2 One function | `value_book(date)` -> one row per trade at that date's marks | yes, from layer 1 |
| 3 Views | header, Ladder, Blotter, Market data, Reconciliation | yes, read-only |

## 2. Valuation spec (layer 2)

`value_book(conn, as_of, marks_source=None)` returns one row per trade with columns:
`trade_id, instrument_id, product, strategy, theme, trade_date, settle_date, status
(OPEN|SETTLED), quantity, fill, mark, mark_date, mark_source, spot, spot_source,
pnl_local, pnl_usd, pnl_spot_usd, pnl_carry_usd, reason`.

Rules, all products:

- A missing mark gives `pnl_usd = NaN` and a non-empty `reason`. Never zero, never a
  substitute. Any NaN row makes every total that includes it Unavailable.
- Marks come from `marks_official` unless `marks_source` is given. `BBG_INTERP` and
  `BNP_BVAL` are never official.
- `status = SETTLED` when the trade's last leg settles before `as_of`; its row comes
  from `realised_pnl` and is never recomputed.
- Trades with `trade_date > as_of` are excluded.

**FX spot / forward** (2 legs, base amount `Q` signed, fill `f`, outright `m` for the
leg's own `settle_date` on `as_of`, spot `S` = quote->USD on `as_of`):

```
pnl_local = Q * (m - f)             quote currency
pnl_usd   = pnl_local * S           S = 1 when quote is USD
```

Carry split: `m_spot` = spot of the pair on `as_of`.
`pnl_carry_usd = Q * (m - m_spot) * S` is the forward-point component still in the
mark; `pnl_spot_usd = pnl_usd - pnl_carry_usd`. The split is descriptive; totals use
`pnl_usd` only.

Crosses (EURSEK): quote is SEK; `S` = USD per SEK = `1 / USDSEK spot` (or `GBPUSD`
directly when the quote is market-quoted XXXUSD). Never invent a USD leg.

Worked examples (must be in the tests):

```
Buy 1,000,000 EUR at 1.1000, value 2026-06-20. June outright 1.1080.
  pnl_usd = 1,000,000 * 0.0080 = +8,000

Buy 1,000,000 USD vs JPY at 150.00, value 2026-06-20. June outright 148.00, spot 149.00.
  pnl_local = 1,000,000 * (148 - 150) = -2,000,000 JPY
  pnl_usd   = -2,000,000 / 149 = -13,422.82

Buy 1,000,000 EUR vs SEK at 11.00, value 2026-06-20. June outright 11.20, USDSEK spot 10.50.
  pnl_local = 1,000,000 * 0.20 = 200,000 SEK ; pnl_usd = 200,000 / 10.5 = +19,047.62

Long 6 ESU6 at 7,528.25, settlement 7,598.50.
  pnl_usd = 6 * 50 * 70.25 = +21,075   (the workbook shows 20,880.16; that is the reconciliation view's number, not ours)
```

**FX swap** (package of near + far): two rows, one per leg pair, each valued as a
forward at its own settle date. Near leg realises on its date, far leg stays open.
Display groups both under `package_id`.

**Future**: `pnl_usd = contracts * multiplier * (settlement_px - fill)`. Mark type
`FUTURE_PX` at the contract's expiry `settle_date`. Realised at close-out fill or
final settlement on expiry.

**Cash** balances have no fill and no row in the blotter. They appear in the ladder
only.

## 3. Close series and periods (layer 2)

- A **close** is the `marks` rows with `as_of_date = d` and `snapped_at` at 17:00
  America/New_York. Marks are appended, never overwritten; a re-pull the same day adds
  rows with a later `snapped_at` and the latest wins within a source.
- `ltd(d) = sum(value_book(d).pnl_usd)`; NaN if any row is NaN. Stored nowhere;
  recomputed from marks so it is auditable. A small cache table is allowed but must be
  invalidated whenever marks or trades for `d` change.
- **Realisation**: when a leg's `settle_date < as_of` and the trade is not in
  `realised_pnl`, freeze it at the outright for its settle date observed on the settle
  date (which equals spot that day), converted at that day's spot. Record rate, date,
  source. If no mark exists on or before the settle date the trade is `unrealisable`
  and every LTD from that date on is Unavailable with the trade id in the reason.
- **Periods**: with `T` = as_of and a trading calendar with holidays,
  `daily = ltd(T) - ltd(prev_bd(T))`, `d5 = ltd(T) - ltd(T - 5 bd)`,
  `mtd = ltd(T) - ltd(last bd of prev month)`, `ytd = ltd(T) - ltd(last bd of prev year)`.
  `trading = sum of pnl_usd for rows with trade_date = T`.
  Each period returns `{value, ref_date, available, reason}`. A first-day book with
  no prior trades has `ltd(ref) = 0`, not Unavailable.
- Per-group periods (by pair, product, strategy, theme) are the same subtraction
  applied to the group's rows.
- `pnl_snapshots` is retired. `realised_pnl` stays, extended for crosses and futures.

### 3a. Decisions of 2026-09-15 (evening review)

- Close is 17:00 America/New_York (changed from 15:00 on 2026-09-15). Reconciliation against BNP is same-date,
  same-close.
- The market value break (our open LTD at the BNP file date minus BNP `mv_usd`, per
  instrument) is the primary comparison and is always shown. BNP day P&L versus our
  daily is informational until two closes exist, then becomes a second break.
- Daily P&L reference `ltd(T-1)` is valued at historical Bloomberg marks pulled on
  demand (one outright per pair per value date at the T-1 pricing date, the Excel's
  PricingDate request generalised), not read from a stored snapshot. The marks table
  is the cache of those pulls. 5d / MTD / YTD follow the same rule as far back as
  history can be pulled; where it cannot, they are Unavailable with the reason.

## 4. Stress (layer 2, ladder side)

From the per-currency USD delta `D_ccy` at spot:

- `move_1pct = D_ccy * 0.01` per currency (sign = P&L if that currency strengthens 1 %
  against USD).
- Named scenarios, each a dict `ccy -> pct move`: `USD +5 % all`, `USD -5 % all`,
  `EM -10 %` (BRL, MXN, TRY, ZAR, IDR, KRW, TWD, INR), configurable in
  `config/stress.yaml`. Scenario P&L = `sum(D_ccy * pct)`. Futures USD delta is added
  as an `ES` line with its own pct. No correlations, no vol.

## 5. Tabs (layer 3)

Every tab is a question. A number appears in exactly one place. Unavailable says why
where it sits.

| Tab | Question | Content |
|---|---|---|
| Header (all tabs) | How am I doing? | LTD, Daily, 5d, MTD, YTD, trading; as-of; mark time and feed status; LTD line chart, collapsible |
| Ladder | What am I long/short and when is it cash? | date x currency grid incl. USD and cash balances at as_of; local delta, spot, USD delta rows; Net/Gross; futures delta line; stress block; cell click -> trades |
| Blotter | Where did the P&L come from? | `value_book(as_of)` rows; filters open/settled, product, pair, strategy, theme, date range; group-by with LTD/Daily/MTD/YTD per group; spot/carry columns; row click -> legs and marks used |
| Market data | Can I trust the numbers? | every mark needed today with value, source, time, status (official/interp/manual/missing); pull button; close completeness per past date; manual entry |
| Reconciliation | Do I agree with BNP and the Excel? | ours vs BNP per instrument (MV, DTD, MTD) with breaks; the literal workbook formula per trade (existing `engine/pnl/pnl.py`, unchanged); neither feeds the header |

Removed from the Cash ladder tab: workbook mark-to-market panel, ledger cards, Exposure
P&L card, workbook FX rates grid (moves to Market data as manual entry).

## 6. Tasks and agent prompts

Four tasks. A and B run in parallel. C waits for both. D is the live check.
Every prompt ends with the full test run; every agent reports the pass count and any
failure verbatim. Agents stay inside their directories; anything they need outside is
listed in the report, not edited.

### Task A: engine (pnl-engine, then reviewer)

> Read CLAUDE.md and docs/BUILD_PLAN.md sections 1 to 4 in full before anything else.
> You own `engine/pnl/` and `tests/test_pnl.py`, `tests/test_ledger.py`.
>
> Build `engine/pnl/valuation.py` with `value_book(conn, as_of, marks_source=None)`
> exactly per section 2: FX spot/forward, swap packages (two rows), futures, settled
> rows from `realised_pnl`, crosses, the carry split, NaN with reason on any missing
> mark. Reuse the `marks_official` view; never read `marks` directly except when
> `marks_source` is given.
>
> Rewrite `engine/pnl/ledger.py` per section 3: realisation for forwards, crosses and
> futures; `ltd(conn, date)`; `period_pnl(conn, as_of)` returning the dict per period
> and `trading`; `period_pnl_by(conn, as_of, key)` for key in `instrument_id, product,
> strategy, theme`. Remove every use of `engine/ladder/exposure` from this module.
> Retire `pnl_snapshots`: drop its DDL from `data/ingest/schema.py` is NOT yours;
> instead stop writing it and list the DDL removal for data-ingest in your report.
> Business calendar: keep Mon-Fri helpers in `aggregate.py` and add a
> `holidays` set loaded from `config/holidays.txt` (one ISO date per line; create the
> file with US 2026 holidays).
>
> Add `engine/pnl/stress.py` per section 4, taking the per-currency USD delta dict
> from `engine.ladder.exposure.build_exposure(...).summary` and a futures USD delta.
>
> Leave `engine/pnl/pnl.py` and `aggregate.py`'s workbook functions untouched; they
> are the reconciliation view.
>
> Tests: the four worked examples in section 2 as exact assertions; a swap package
> whose near leg realises and far leg stays open with LTD continuous across the
> settlement day; a missing outright on one trade making `ltd` NaN with the trade id
> in the reason; periods on a first trading day equal LTD; a holiday in
> `config/holidays.txt` shifting `prev_bd`; a cross valued without a USD leg; stress
> scenario arithmetic. Fixtures use synthetic marks with `source = BBG_BFXFORWARD` /
> `BBG_BDH`.
>
> Finish with `py -3 -m pytest tests/ -q`. Failures in `tests/test_ui.py` and
> `tests/test_backfill.py` are expected until tasks B and C land; report them verbatim.
> Report: public function signatures, the `pnl_snapshots` removal note, any schema
> change you need (a `theme` column on `trades` is coming from task B, code against it
> defensively with `COALESCE(theme, '')`).

Reviewer prompt, after A:

> Read docs/BUILD_PLAN.md section 2 and 3. Review `engine/pnl/valuation.py` and
> `engine/pnl/ledger.py` only. Confirm each formula against the spec and the four
> worked examples, confirm settled rows are never recomputed, confirm no NaN is ever
> replaced by zero, confirm crosses never get a synthetic USD leg, confirm LTD is
> continuous across a settlement. Report findings ranked by severity; do not edit.

### Task B: data (bbg-data and data-ingest, one agent each, parallel with A)

bbg-data:

> Read CLAUDE.md and docs/BUILD_PLAN.md sections 2, 3 and 5. You own
> `data/bloomberg/` and its tests.
>
> `data/bloomberg/live.py::build_requests` must request one `FWD_OUTRIGHT` per
> `(pair, settle_date)` for every open FX leg plus `SPOT` per pair and `FUTURE_PX` per
> open future at its expiry. Drop the `WORKDAY(as_of,5)` maturity request entirely
> (the reconciliation view's marks are entered manually on the Market data tab).
> Retire `data/bloomberg/backfill.py`'s snapshot writing; backfill now only writes
> marks for past closes (interpolated tenor history is `BBG_INTERP`, never official)
> and calls `engine.pnl.ledger.realise_settled` if importable, else skips with a note.
> Every mark row keeps `source` and `snapped_at` resolved from 17:00
> America/New_York for its date.
>
> Add `data/bloomberg/inventory.py::mark_inventory(conn, as_of)` returning one row per
> mark the book needs on `as_of` (from the same open-leg query) with
> `instrument_id, settle_date, mark_type, value, source, snapped_at, status` where
> status is `OFFICIAL | INTERP | MANUAL | MISSING`, plus
> `close_completeness(conn, start, end)` -> one row per business day with counts of
> needed vs present official marks. This feeds the Market data tab.
>
> Manual entry: `data/bloomberg/manual.py::write_manual_mark(conn, as_of, instrument_id,
> settle_date, mark_type, value)` writing `source = 'MANUAL'`. Note in the docstring
> that MANUAL is official only for DELTA and PREMIUM per the contract; for SPOT /
> FWD_OUTRIGHT it is visible on the Market data tab but not used by valuation unless
> `marks_source='MANUAL'` is passed. Do not change `marks_official`.
>
> Tests for build_requests coverage (every open leg date requested, no shared
> maturity), inventory statuses, completeness counts. Finish with
> `py -3 -m pytest tests/ -q`; report verbatim.

data-ingest:

> Read CLAUDE.md and docs/BUILD_PLAN.md sections 1, 2 and 5. You own `data/ingest/`
> and `tests/test_ingest.py`, `tests/test_upload.py`.
>
> 1. Schema: add `theme TEXT NOT NULL DEFAULT ''` to `trades` with a migration for
>    existing databases; add table `instrument_theme (instrument_id PRIMARY KEY, theme)`
>    so a new trade in a pair inherits the pair's theme unless set. Remove the
>    `pnl_snapshots` DDL. Extend `realised_pnl` with `product TEXT NOT NULL DEFAULT
>    'FX_FWD'`, `mark_type TEXT NOT NULL DEFAULT 'SPOT'` and make `usd_entry_amount`
>    nullable-by-sentinel 0.0 for crosses and futures (document it).
> 2. Swap packaging: implement the CLAUDE.md `package_id` rule in
>    `data/ingest/swaps.py::package_swaps(conn)` and run it at the end of every BNP
>    upload; candidates with more than one match per side go to a `swap_review` table,
>    never auto-grouped. A packaged pair keeps two `trades` rows, sets
>    `product = 'FX_SWAP'` and the shared `package_id`.
> 3. Workbook futures fills: `data/ingest/xlsx_futures.py` reads `All FX trades` rows
>    whose pair ends in ` Index`, recovers contracts from the cell formula
>    (`=<n>*E<row>*50`) or from `C/(50*E)` when only a value is saved, and inserts
>    `trades` with `source='XLSX'`, `trade_id='XL-<row>'`, one `NOTIONAL` leg per the
>    contract. Idempotent on re-upload. FX rows of the workbook are still NOT imported.
> 4. Provide `set_theme(conn, trade_id|instrument_id, theme)` for the UI.
>
> Tests for the migration on an existing DB, swap rule (grouped, round trip not
> grouped, ambiguous -> review), futures fill recovery from both formula and value,
> theme inheritance. Finish with `py -3 -m pytest tests/ -q`; report verbatim.

### Task C split (2026-09-15, user request): five narrow UI agents

C1 to C4 run in parallel and own disjoint files; C5 wires them and runs last.
Shared rules for all five: no calculation in `ui/`, only calls into engine and data
functions; every number appears once; Unavailable shows its reason in place; each
agent writes its own test file and does not touch `ui/app.py` or `tests/test_ui.py`
(C5 only). Engine signatures: `engine.pnl.valuation.value_book(conn, as_of,
marks_source=None)`, `engine.pnl.ledger.{ltd, period_pnl, period_pnl_by,
realise_settled, realised_rows}`, `engine.pnl.stress.{move_1pct, load_scenarios,
run_scenarios}`, `engine.ladder.exposure.{build_exposure, portfolio_totals,
ladder_usd_equivalent}`, `engine.ladder.exposure_adapter.records_from_db`,
`data.bloomberg.inventory.{mark_inventory, close_completeness}`,
`data.bloomberg.manual.write_manual_mark`, `data.ingest.themes.set_theme`.

| Agent | Owns | Builds | Tests |
|---|---|---|---|
| C1 Ladder | `ui/tabs/cash_ladder.py`, `ui/tabs/exposure.py`; delete `ui/tabs/ledger.py` | strip workbook panel, ledger cards, P&L card, diagnostics and rates grid from the tab; fix removed-field callers; cash balances row at as_of; futures USD delta line; stress block from `engine.pnl.stress`; keep `build_layout(default_date)` and `register_callbacks(app, get_db_path)` signatures | `tests/test_ui_ladder.py` |
| C2 Header + Blotter | new `ui/tabs/header.py`, new `ui/tabs/blotter.py` | header block `header.layout()` + `header.register_callbacks(app, get_db_path)` showing LTD, Daily, 5d, MTD, YTD, trading from `period_pnl`, as-of, mark time, collapsible LTD line chart from `ltd` over recent business days; blotter tab with `value_book` rows, filters (open/settled, product, pair, strategy, theme, date range), group-by with per-group LTD/Daily/MTD/YTD from `period_pnl_by`, spot/carry columns, row expand with legs and marks used, inline theme edit via `set_theme`, swap packages as one expandable row | `tests/test_ui_blotter.py` |
| C3 Market data | `ui/tabs/market_data.py` (rewrite) | `mark_inventory` table with status colours, pull button and feed status (move from ladder toolbar; reuse `data.bloomberg.live`), `close_completeness` strip, manual entry form via `write_manual_mark`, Bloomberg diagnostics panel moved here | `tests/test_ui_market_data.py` |
| C4 Reconciliation | new `ui/tabs/reconciliation.py`; `ui/tabs/pnl.py` content absorbed then file deleted; `ui/workbook_rates.py` stays | ours vs BNP per instrument (`positions` source BNP vs `value_book` grouped by instrument, break column); the workbook panel (`engine.pnl.pnl.ltd_per_trade`, `engine.pnl.aggregate.{aggregate_by_pair, period_pnl}`, `engine.ladder.valuation`) with the manual rates grid; neither feeds the header | `tests/test_ui_reconciliation.py` |
| C5 Wiring | `ui/app.py`, `tests/test_ui.py`, `docs/HOW_IT_WORKS.md` §3-4 | `VISIBLE_TABS = Ladder, Blotter, Market data, Reconciliation`; header above tabs; register all modules; remove Overall book and placeholders; fix `ensure_schema` test; rewrite `tests/test_ui.py` as smoke tests of the assembled app; full suite green | `tests/test_ui.py` |

The original single-agent Task C prompt below is kept for reference only.

### Task C: UI (ui-shell, after A and B)

> Read CLAUDE.md, docs/BUILD_PLAN.md section 5 in full, then the public signatures in
> `engine/pnl/valuation.py`, `engine/pnl/ledger.py`, `engine/pnl/stress.py`,
> `engine/ladder/exposure.py`, `data/bloomberg/inventory.py`, `data/ingest/swaps.py`.
> You own `ui/` and `tests/test_ui.py`.
>
> Build the four tabs and the header exactly as the table in section 5. Rules: every
> number appears once; anything Unavailable shows its reason in place; no calculation
> in `ui/`, only calls into engine and data functions. Keep the upload strip.
>
> Cash ladder tab: delete the workbook mark-to-market panel, the ledger cards, the
> Exposure P&L card, the workbook FX rates grid and the Bloomberg diagnostics panel
> from this tab. Fix all callers of the removed `exposure_pnl`, `usd_delta_entry`,
> `usd_entry_amount` fields. Add cash balances as a row at `as_of`, the futures delta
> line, and the stress block under the ladder.
>
> Blotter tab: `value_book` rows; filters; group-by selector with per-group
> LTD/Daily/MTD/YTD from `period_pnl_by`; spot and carry columns; row expand shows
> legs and the marks used with source and time; inline theme edit calling
> `set_theme`. Swap packages shown as one expandable row.
>
> Market data tab: `mark_inventory` table with status colours, the pull button and
> feed status moved here, `close_completeness` calendar strip, manual entry form
> calling `write_manual_mark`, Bloomberg diagnostics panel moved here.
>
> Reconciliation tab: ours vs BNP per instrument from `positions` (source BNP) against
> `value_book` grouped by instrument, break column; the existing workbook panel
> (`engine/pnl/pnl.py` + `engine/ladder/valuation.py`) moved here unchanged with its
> manual rates grid; the Overall book tab's content moves here too and that tab is
> removed.
>
> Header on every tab: five figures plus trading, from `period_pnl`; LTD line chart
> from `ltd` over the completeness range, collapsible.
>
> Update `docs/HOW_IT_WORKS.md` sections 3 and 4 to describe the new tabs (housekeeper
> owns docs; for this task you are authorised to edit that file only).
>
> Rewrite `tests/test_ui.py` for the new layout. Finish with
> `py -3 -m pytest tests/ -q`; all tests must pass; report the count.

### Task D: live check (user, on the Bloomberg machine)

Run the pull after 17:00 New York, upload the next BNP file, and compare on the
Reconciliation tab: ours vs BNP per instrument, and ours vs the recalculated workbook
per trade. Record breaks in `docs/open-questions.md`. Only after this does the
workbook get retired.

## 7. Later, same structure

IRS: rows in `value_book` with `pnl_usd = PV(as_of) - PV(trade_date)`; own tab with
DV01. Options: rows with premium P&L; own tab with Greeks; delta joins the ladder's
Net/Gross via the CLAUDE.md union query. Neither changes layers 1 to 3.
