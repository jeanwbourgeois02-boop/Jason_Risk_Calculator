# risk-monitor (Jason)

Commodity relative-value risk monitor for Jason, a paper trader in metals, energy and agriculture: listed futures and their calendar and inter-commodity spreads, options on futures, LME forwards and FX hedges (user, 2026-09-24), base currency USD. Python, SQLite, Dash. It shows one P&L (per-trade valuation, an LTD line, Daily / 5d / MTD / YTD / trading), the positions by commodity and contract month, a cash ladder (delta exposure, cashflow timing, settled cash, stress) and a blotter (every trade with its P&L and Greeks).

The app was forked on 2026-09-23 from a macro FX / rates risk monitor (another trader, fund NMMF) and is being converted under "Commodity conversion plan" below; its screens are being rebuilt for relative value under "Screens redesign plan". Until a phase lands, the sections it names still describe the macro app as it is; the plan's decisions already bind.

This file is the rulebook: what is true of the app now and what must not change without the user's say-so. It is not a changelog. History lives in git and `docs/bnp-excel-removal.md`; open items live in `docs/open-questions.md`, not here; the valuation spec is `docs/BUILD_PLAN.md` sections 2 to 4, which agree with "P&L conventions" below. Reference input: the synthetic Jason book `data/sample/blotter_sample.csv` (a real commodity export has not been seen yet).

## Hard rules

1. **One trade source.** The uploaded blotter export, parsed by `data/ingest/blotter.py`, is the app's only trade file. The only other way a trade enters the book is the Blotter's Manual entry sub-tab (`source = 'MANUAL'`). There is no BNP file, no Excel workbook and no second feed (user decision 2026-09-17: "no bnp fall back - that excel and everything linked to it need to go"); nothing may reintroduce one. `data/raw/HA_PNL_*.csv` and `data/raw/HA-portfolio vJean.xlsx` stay on disk as untracked reference material that nothing in the app reads.
2. **A missing mark is worked out from the near marks, never from another source, and never silently.** No fallback mark *source* for P&L or delta, ever, and nothing is written to `marks` that Bloomberg or the app's own pricers did not produce. But a mark that is not on file for the exact key is estimated on the fly from the official marks nearest to it (user decision 2026-09-22: "if there is no price, we should always interpolate/extrapolate with near marks", then "use all marks available to extrapolate"; `engine/pnl/valuation.py::_mark_near`): a forward first along that day's own curve (spot at the spot date, which is `engine/pnl/calendar.py::spot_date`, the app's one rule since 2026-09-22: T+2 weekdays, T+1 for USDCAD / USDTRY / USDPHP / USDRUB, rolled forward off a `config/holidays.txt` holiday, the rule the backfill's computed tenor dates follow too (`data/bloomberg/fwd_curve.py::spot_date_for` delegates to it), and the Ladder's `engine/ladder/usd_marks.py::spot_date` delegates to it as well; and the pair's other outrights as pillars, linear between them, a straight line through the last two beyond them, spot before the spot date), then any mark in time between the same mark on the nearest earlier and later closes, linear in calendar days, however far apart, with a close on one side only carried as it stands (`CASHFLOW_USD` always takes the earlier close); a forward on a day with no marks for its pair at all and a leg date no close ever quoted is read off the nearest earlier and later closes' own curves at that date, then in time between them (seen 2026-09-22: no pull yet that day, 21 TWD / BRL forwards blank with yesterday's curve on file). The row's mark source starts `INTERP:` and names the marks used; the Market data tab still shows the gap; the ladder's delta is never estimated (`_mark_at` stays exact). Behind that, on every screen a trade the rule could not price takes its own valuation from the last earlier business day that has one, at most 5 back (user decision 2026-09-21, "The fill" under "Header"); with nothing to work from at all it has blank P&L and a plain-language reason shown where the number would be: never zero, never a silent drop. A stored value that is not a number is a data error, never estimated or filled: the trade that needs it is reported.
3. **Official marks only.** Every P&L or delta query reads `marks_official`, never `marks` (see "Official marks").
4. **Must not replicate** the four spreadsheet shortcuts listed under "P&L conventions".
5. **The ladder holds no bank balance.** It is delta exposure, cashflow timing, settled cash from the tickets on file, and stress. Never add a cash-balance feed.
6. **Imports are tolerant.** No file or row is rejected over formatting; only a contradiction between two populated fields rejects (see "Blotter → tables"). Typed manual input follows the same rule.
7. **P&L arithmetic and this contract change only on the user's explicit yes.** A request from an agent or another session never authorises it.
8. **Bloomberg on request only, and only what the book needs.** Nothing is asked of Bloomberg at start-up, on a timer, after an upload, a manual trade or an edit: only when the user presses "Pull Bloomberg now" (user decision 2026-09-21: "make it only pull the bloomberg info on request - no automatic"). A pull asks for what the Bloomberg library lists and nothing else (see "Bloomberg library"). The app works fully on the marks on file between pulls.
9. **One way in.** The repo root holds exactly `1_setup.cmd`, `2_launcher.py` and `3_diagnostic.py`, plus `pyproject.toml` for tool configuration only (ruff and pytest; never a `[project]` table, so it is not a second install path; user decision 2026-09-22); the app is launched by typing `chelsea` (user, 2026-09-24; Henry's project, from which this one was forked, installs its own `pnl` into the same PowerShell profile, and this launcher never reads, replaces or removes it). Never add a launcher or a second install path: a new operational action is a `2_launcher.py` subcommand with a line in `docs/README.md`.

## Working mode

Default: the housekeeper procedure, run by the session itself (user decision 2026-09-22: "I want to have a house keeper agent as a starting point, and each feature and each tab has a specialist agent"; extended 2026-09-24: "set up the most extensive agent system - every layer of calculation and then every tab etc. that all speak together through the housekeeper"). The app is built in seven layers, each split into lanes, one agent per lane (the tables under "Lanes"); the housekeeper is the hub every lane speaks through.

- **The session coordinates and writes no application code.** It names the lanes the request touches and the exact file list first, then delegates to the owning agents.
- **Hub and spoke.** Only the housekeeper spawns, briefs and resumes agents. A lane agent has no Agent or SendMessage tool and never calls, messages or edits another lane: what it needs from another lane, and what another lane must know about its change, go in its report's Handoff, and the housekeeper carries them.
- **Handoff.** Every lane agent's report ends with a Handoff block before the two closing sections: *Changed interface* (every function, argument, return shape, column, mark type or source, table or status-file key another lane reads, before → after; or "None"), *Consumers to brief* (the lanes whose "Reads" column names this one), *Requests* (file, change, why and owning lane for each change needed outside its files; or "None"), *Blocked on* (what it needs from which lane before it can finish; or "Nothing").
- **Routing.** Work runs bottom-up by layer: the lowest layer the request touches goes first. Each changed interface is walked downstream through the "Reads" column and every consumer is briefed with the producer's Handoff verbatim; that is how the lanes speak to each other. Requests go to the owning lane, never done by the asker. Lanes whose files do not overlap and that do not read each other run in parallel; a blocked lane is resumed with the answer once the lane it waits on reports. Within a layer the same holds, and the screens (layer 7) always come last, because they read the engine's output shape.
- **Fast path.** A request that touches one file goes to that file's one owning lane and nobody else, with no reviewer: the path that keeps a small fix quick.
- **Tests.** Each lane runs only its own test files; the session runs `py -3 -m pytest tests/ -q` once at the end and reports the pass count. The reviewer runs only on changes to P&L arithmetic in `engine/`.

Clean-up is a lane of its own, by kind of change rather than by file: `infra` (`.claude/agents/infra.md`; user, 2026-09-22: "make sure the housekeeper can trigger that too") makes the mechanical, behaviour-neutral changes anywhere (dead code, unused imports, duplicate helpers, stale docstrings and references, module splits with every caller updated, ruff / pytest / git configuration, the `.claude/` tooling) and owns the root scripts, `config/`, `tools/`, the shared test fixtures, the golden book (`tests/golden/`, `tests/golden_book.py`, `tests/test_golden_book.py`, `tests/test_health.py`), `pyproject.toml`, `.gitattributes`, `.gitignore` and `.claude/settings.json` outright; formulas and SQL in `engine/`, pricing, official mark sources, schema DDL, CLAUDE.md and any test's expected value are out of its lane and come back as findings. It works from the health audit (`py 2_launcher.py health`) and proves every change with the golden book and the full suite. The golden book's pinned file, `tests/golden/book.json` (the sample blotter at synthetic marks, every valuation pinned), is regenerated only on the user's explicit yes, never by an agent (user decision 2026-09-22): a regeneration that made a P&L change disappear would defeat the proof, so hard rule 7 covers it. The session spawns it only when the user asks (after a feature lands, or as a weekly full pass), never on its own, after the feature commit, on a clean tree with no other agent running. `.claude/agents/housekeeper.md` is the same procedure for a detached run, when the user names it.

**Do not overdo it (user rule, 2026-09-18: "you're doing way too many random things and checks instead of just fixing the problems").** Fix what the user asked for, run the full suite once, push, report in a few lines, and stop. Scope is the user's list. Anything else found on the way (a reviewer finding, a hardening idea, a cosmetic issue) goes in a short "found, not done" list for the user to choose from; it is not started, and a finished agent is not resumed with new items, without the user's yes. The one exception is a finding that makes the requested fix itself wrong: say so in one line and fix that. Brief an agent for the fix and the tests that prove it: no browser proofs, fuzzing or per-agent full-suite runs unless the user asks; whoever spawned the agents runs the full suite once at the end. The reviewer runs on P&L arithmetic changes, and its findings are relayed to the user, not dispatched. Give one time estimate and keep to it by cutting scope, not by extending. Status updates are a few lines: no tables, no re-listing of open questions each time.

**Agent model (user decision 2026-09-18, reversing the earlier Sonnet-for-cost rule).** Every agent runs on Fable 5.1 at high effort (user decision 2026-09-22, "its too slow now, turn to high effort", down from extra-high): each `.claude/agents/*.md` carries `model: fable` and `effort: high`, and a spawn passes `model: "fable"`. Never Sonnet or Haiku. Say plainly which model is doing the work when asked.

**How every reply ends (user rule, 2026-09-18).** Every final message ends with these two sections, in this order, as bullet points. This applies to every chat session and to every agent's final report; whoever spawned an agent relays its two sections to the user instead of swallowing them.

- **What was done**: what changed and where, what was verified and how (the pytest pass count goes here), and anything skipped or left unverified, said plainly.
- **What needs your input**: only the decisions, approvals or facts the user alone can give, one bullet each, with a recommendation where there is one. If there is nothing, write "Nothing". A low-stakes, reversible choice is not input: take the recommended option and report it under "What was done" (user, 2026-09-18: "i dont have strong opinions i imagine your recs are the best"). Hard rule 7 still stands: P&L arithmetic and this contract always need the user's yes.

The working copy is `C:\Users\jeanw\Ninemasts\Dashboards\Jason Risk Monitor`; GitHub `main` is the backup, pushed at the end of each task.

When agents or several sessions work in parallel, the tables under "Lanes" define the lanes. State your file list to the other sessions first and keep to it; message the owner and wait before editing outside your lane; commit by explicit path only (the git index is shared: run `git diff --cached --stat` immediately before every commit, never `git add -A`); preserve each file's line endings.

## Commodity conversion plan

User, 2026-09-24: "make a plan and build as many specific agents that will own a specific section of the calculations and UI - and then I want you to get started on this plan and coordinate the agents". The housekeeper runs it through the lanes (the commodity lanes are marked ◆ under "Lanes"), phase by phase, bottom-up by layer inside each phase; a phase is committed and pushed when its full suite is green against the baseline of known environmental failures.

**Decisions (user, 2026-09-24).**
- Products in scope: listed futures and their spreads (calendar, inter-commodity, China against the West), options on futures, LME forwards, FX hedges.
- Futures close: Bloomberg's daily `PX_LAST`, as today (not the exchange settlement, not one snapshot time). Past closes stay stamped 17:00 New York; the day still turns at 17:00 New York / 05:00 Hong Kong, which falls after the Chinese night session and before the day session.
- Non-USD futures and listed options: P&L in the quote currency, converted at spot of the valuation date ("P&L conventions → Futures").
- The macro trader's code and data leave the working tree: rates (IRS, swaptions, caps), NDFs, the FX-swap package rule, equity index futures and options, the macro sample blotter, its Bloomberg snapshot. The golden book is regenerated on a synthetic commodity sample (this is the user's yes under hard rule 7). Git history keeps them; nothing is force-pushed.
- Later the same day (user, answering four questions before "go through the entire plan before i review"): a settled future converts at the spot of its expiry date (the last spot on or before expiry, as FX), not of its last price's date; options on commodity futures are product `CMDTY_OPTION`, valued like a future at Bloomberg's own option price, Greeks from Black-76 / American at the implied vol; LME forwards follow the FX-forward rule (tonnes × (official outright at the prompt date − fill), converted at spot, frozen at the prompt date, cash on the ladder on the prompt date); an old database's leftovers of the retired products show as they were frozen (an NDF keeps its figure) or, for a leftover open swap, as a blank row with its reason, never dropped. Taken on the recommended option (low stakes): an FX swap choice on the Manual entry screen, CNY and MYR moves in the commodity stress, an expired contract the ledger has frozen leaves the Expiries tab, CNH stress at the Asian-EM size.
- The contract universe is seeded from the sibling research app `../Commodity Dashboard/rvapp/universe/` (202 contracts, 264 spreads), assumed to be Jason's until he says otherwise. Only 11 of its Bloomberg roots are verified on a terminal.

**Phase 1: Jason's futures load and price correctly.**
1. contract-master: `config/contracts.csv` (the universe: exchange, currency, contract size, unit, price scale, multiplier, Bloomberg root and key, month cycle) and `data/contracts/` (symbol → contract, canonical id `CLZ26 Comdty`, Bloomberg request ticker, a conservative expiry until Bloomberg's own dates are on file). exchange-calendars: `config/calendars/`, `engine/calendars/`. pnl-valuation, pnl-ledger: non-USD conversion (reviewer). book-positions: futures delta in USD at spot. These four run in parallel.
2. ingest-parser: commodity futures resolved through contract-master, the fund filter from `config/book.yaml`, a synthetic commodity sample. bbg-library: the USD conversion SPOT of a non-USD future. bbg-live: Bloomberg's contract dates (`FUT_LAST_TRADE_DT`, `FUT_NOTICE_FIRST`) on request, stored through contract-master.
3. curve-positions, then ui-curve and ui-shell: a Curve tab with the positions by commodity × contract month. expiry-monitor, then ui-expiries: first notice, last trade and option expiry alerts.

**Phase 1: done 2026-09-24** (673fab2, 89e60e6, e660974).

**Phase 2: the tickers, the Bloomberg check, and the macro trader's code and data out** (user, 2026-09-24: "start - make the code with the tickers you think are best - and add a bloomberg diagnostic tool I will be able to use so that I can diagnose when I finally have access to bloomberg"). contract-master replaces every placeholder Bloomberg root with its best guess (still `bbg_verified` false) and gains the function that applies a fixes worksheet; bbg-ticker-check builds the check the user runs at the terminal (`py 2_launcher.py bbg-check`, wired by infra), which compares each root's name, exchange, currency, contract size and value per point with `config/contracts.csv` and writes the worksheet. Then the removal. It starts with ingest-parser replacing `data/sample/blotter_sample.csv` (the macro trader's real blotter) with a synthetic commodity, FX-hedge and FX-option book at the same path. After that it runs top-down by layer, so no lane removes what another still imports; each owning lane removes its own part and re-pins its own tests to the new sample:
1. The screens: ui-rates deletes the Rates sub-tab and retires. The other tabs drop their NDF, IRS, DV01, equity-index and SPX displays.
2. Risk: risk-metrics drops the rates and equity underlyers.
3. Exposure: ladder-grid and ladder-exposure drop the NDF rules. book-positions drops the equity index and DV01 lines.
4. P&L: pnl-valuation, pnl-ledger and pnl-series drop IRS and NDF valuation (reviewer).
5. Pricers: rates-exotics deletes `engine/rates_vol/` and retires. rates-pricer keeps only the OIS discount curves the option pricers read.
6. Market data: the Bloomberg lanes drop NDF_1M / NDF_FIX, the fixings, the rates vols and the swap re-pricing. bbg-snapshot empties `data/bbg_snapshot/`.
7. Trades in: ingest-booking deletes `irs_direction.py` and the FX-swap package rule. ingest-parser drops the IRS, equity-index, SPX and NDF parsing.
8. infra wires `bbg-check`, trims the macro checks from `tools/`, and regenerates the golden book on the new sample.
9. The session rewrites the macro sections of this file and `docs/`.

The generic listed-option path (P&L from Bloomberg's option price, Greeks from implied vol) stays for Phase 5's options on futures, as do the FX forward and cash-ladder paths (FX hedges, LME). FX options are not in scope but were not approved for removal: they stay dormant until the user decides.

**Phase 3: relative value.** spreads-engine, then ui-spreads: spreads found in the book (calendar legs, inter-commodity legs with their ratios from `config/spreads/`), P&L and leftover outright per spread. ui-header: gross notional, net outright by sector, open spreads, the next first notice. ui-blotter: the Futures sub-tab by commodity. ui-market-data: the futures curve per commodity. book-positions: commodity lines in place of the equity index line.

**Phase 4: risk.** risk-history: commodity settlement history per contract month from the research app's `price_daily` (read-only, like the old nm-dashboard cache). risk-metrics: per commodity, spread and sector, commodity shock days, Jason's vol target. commodity-stress: outright, curve-shape, spread and CNH scenarios and historical replays. ui-risk renders both.

**Phase 5: the other products.** listed-options-pricer, options-store, ui-options: options on futures (P&L from Bloomberg's option price, Greeks from its implied vol, Black-76 or American). lme-forwards with bbg-curves: prompt-date forwards (its P&L formula needs the user's yes first). FX hedges on the existing FX forward path. The declining delta of monthly-average contracts in curve-positions. margin-limits: initial margin with spread credits, exchange position limits.

**Phases 3-5: built 2026-09-24**, the rules approved the same day (Decisions above) and described in the sections below. The golden book's re-pin on the grown sample (4 options on futures and 3 LME forwards added) waits on the user's yes. Everything Bloomberg-linked (the tickers, fields and price scales confirmed at a terminal) and the import of Jason's real blotter are parked (user, 2026-09-24: "everything bloomberg linked and excel imported will come but not now"): the code for them is built on best guesses and runs on request only.

**Gates the user holds** (tracked in `docs/open-questions.md`): a real blotter sample and Jason's fund code (Phase 1 is built on a synthetic sample until then); Jason's vol target, stress scenarios, margin rates and desk / exchange limits (placeholders or unset until then: `config/risk.yaml`, `config/commodity_stress.yaml`, `config/limits.yaml`); the Bloomberg roots and price scales checked on the Bloomberg PC.

## Screens redesign plan

User, 2026-09-25: "i feel like the calculations are good but the ui here is not good - go for a deep think about the ui how it should be for commodities rv", then, on the proposal made that day, "ok lets make a plan - get the agents going - and get started". The screens are rebuilt for a commodity relative-value book. The engine's numbers do not change (hard rule 7 untouched): what changes is what the screens show, where, and how. Until a phase lands, "Tabs as views" still describes the screens as they are; the decisions below already bind. The housekeeper runs it through the screen lanes (and, for Phase B, spreads-engine and risk-history), phase by phase, committed and pushed when the full suite is green against the environmental baseline.

**Decisions (user, 2026-09-25: the proposal's recommended options, taken on "ok").**
- **The spread is the unit.** P&L, exposure and risk read spread → sector → book.
- **One place per number.** A figure is shown on one screen; the others link to it.
- **Numbers first.** A figure the engine could not give, or a sum that leaves something out, shows a short visible marker (`n/a`, `excl. 3`, `filled 2`) with its sentence on hover, and each tab gathers its reasons in one collapsed "Data issues (N)" drawer. A section's definitions sit on hover of its title, never as a paragraph above the table. Nothing is silent (hard rule 2 stands). This replaces the visible caption sentences described under "Header".
- **Natural units.** Spreads in their quote unit ($/bbl, ¢/bu, a ratio), exposure in lots and USD per 1-unit move, money on summary screens in k / m (the full figure in every CSV). Trade rows keep full figures.
- **Tabs, in this order:** Book (Phase B; the app opens on it), Spreads, Curve, Risk, Expiries, Blotter, FX & cash (today's Ladder), Data (today's Market data). Until Book lands the app opens on Spreads. This replaces the macro book's order of 2026-09-22 (Blotter first, Ladder second).
- **Header:** one slim row. The P&L figures, gross commodity notional, net outright, open spreads, and chips for the next expiry event, the marks missing and (Phase B) VaR against the vol target. FX Net / Gross USD delta leave the header for FX & cash.
- **Research context.** The research app's spread statistics (z-score, 5-year percentile, half-life, the day's move in σ: `spread_stats` in `../Commodity Dashboard/var/rv.sqlite`, whose spread ids are the `config/spreads/` template ids) are shown beside the book's spreads, read-only and labelled "research". They are context: never a mark, never in P&L or delta (hard rule 2 untouched), and they ask Bloomberg nothing.
- **Reader.** Built for Jason as the daily reader (levels, entries and moves first), with the overseer's view on Book and Risk.

**Phase A: clean-up, screens only.** ui-shell first: the tab order and names, and the shared helpers (a title with its definitions on hover, the Data issues drawer, the marker, the k / m formats). Then the tabs in parallel, each in its own files:
- ui-header: the slim header; FX Net / Gross out.
- ui-blotter: no P&L cards on Total book (the header is the total book); the trade table in commodity terms (commodity, exchange, contract, lots or tonnes, fill, mark, P&L in the contract's currency and in USD, plain product names).
- ui-risk: commodities before currencies, commodity scenarios before FX scenarios, the history-folder and "not included" lists in the drawer, single-line rows.
- ui-expiries: single-line rows, every column in sight at 1680 px, business days to the alert beside the level.
- ui-ladder: the tab becomes FX & cash; the open futures table and the futures rows of its scenario table leave (they are on Curve and Risk).
- ui-market-data: the tab becomes Data; data health first, the futures curves before the FX pair.
- ui-curve, ui-spreads: definitions to hover, reasons to the drawer, k / m.
In the parallel wave no lane edits the shared `tests/test_ui.py` or `tests/test_app.py`; each hands its changes to them to the housekeeper, who runs those fixes one lane at a time.

**Phase B: the RV core.**
- spreads-engine: a spread's identity across trade dates (one open position per spread, entries averaged); its level at entry, at the previous close and now, in its quote unit, from the fills and the official marks (the research app's spread formula, so the levels compare with its statistics); USD per 1-unit move of the spread.
- risk-history: the research app's spread statistics, read-only.
- Then ui-spreads: one row per spread grouped by family, with entry, now, the day's move, σ, z-score, percentile and P&L, and a drill-down with the spread's history.
- A new ui-book lane (`ui/tabs/book.py`): today's P&L by spread, net outright by sector, alerts, top movers.
- ui-header: the VaR chip.

**Phase C: charts.**
- ui-curve: the commodity × month heatmap, and each commodity's curve with the positions under it.
- spreads-engine then ui-spreads: the P&L history per spread.
- ui-market-data: market data by commodity.

## Data contract

### Tables

All dates ISO `YYYY-MM-DD`, all amounts signed (`+` = receive / long). No column is nullable; "not applicable" uses the documented sentinel. `data/ingest/schema.py` is the executable copy of this section: a column added to its DDL is migrated onto existing databases automatically (`_migrate_columns`), so every `NOT NULL` column added later carries a `DEFAULT`.

```sql
instruments (
  instrument_id   TEXT PRIMARY KEY,   -- 'USDCNH', 'EURGBP', 'XAUUSD', 'CLZ26 Comdty' (a commodity future: the
                                      -- canonical contract id, two-digit year), 'EURUSD111826C-500041'
                                      -- (FX option: one instrument per option trade), 'CLZ26C 75 Comdty'
                                      -- (an option on a future: contract-master's canonical option id), 'LME:CA'
                                      -- (an LME metal: one instrument, many prompts, like an FX pair), 'CASH-EUR'
  asset_class     TEXT NOT NULL,      -- FX | FUTURE | FX_OPTION | EQ_OPTION | CMDTY_OPTION | LME_FWD | CASH
  base_ccy        TEXT NOT NULL,      -- unit of trades.quantity: 'USD' for USDCNH, 'EUR' for EURUSD, 'XAU';
                                      -- the contract root id for a commodity future, an option on one or an LME metal
                                      -- ('NYMEX:CL', 'LME:CA'; config/contracts.csv); base of pair for FX options
  quote_ccy       TEXT NOT NULL,      -- currency of trades.price and of local P&L (CNY for SHFE copper)
  multiplier      REAL NOT NULL,      -- 1 for FX, FX options and LME (quantity in tonnes); a future's (and its options')
                                      -- quote-currency amount per 1.0 of price per
                                      -- contract, from config/contracts.csv (1000 for WTI, 50 for CBOT corn in cents)
  is_ndf          INTEGER NOT NULL,   -- always 0 since 2026-09-24 (NDFs left the app); kept for old databases
  bbg_ticker      TEXT NOT NULL,      -- 'USDCNH Curncy'; a future's Bloomberg live form 'CLZ6 Comdty', an option's
                                      -- 'CLZ6C 75 Comdty', an LME metal's cash ticker 'LMCADY Comdty'; '' when its root
                                      -- has no Bloomberg ticker yet
  expiry_date     TEXT NOT NULL       -- '9999-12-31' for perpetual (FX pairs, LME metals, cash); a future's or an
                                      -- option's last trade date (Bloomberg's once stored; for an option, else the
                                      -- broker symbol's own expiry; else contract-master's conservative estimate)
);
-- Always name the columns when inserting into instruments: an old database can carry
-- extra columns, and a positional INSERT fails every upload on it. An option on a future also writes its
-- underlying future's row, instrument only (no trade), never overwriting one on file.

instrument_options (                   -- option terms, kept out of instruments
  instrument_id   TEXT PRIMARY KEY REFERENCES instruments,
  strike          REAL NOT NULL DEFAULT 0,             -- 0 = not known (never fabricated)
  option_type     TEXT NOT NULL DEFAULT '',            -- CALL | PUT | ''
  barrier_level   REAL NOT NULL DEFAULT 0,             -- barrier / touch level; 0 = n/a
  avg_start_date  TEXT NOT NULL DEFAULT '9999-12-31',  -- Asian averaging start; sentinel = n/a
  payoff          TEXT NOT NULL DEFAULT 'VANILLA'      -- VANILLA | DIGITAL | BARRIER_KI | BARRIER_KO
                                                       -- | ASIAN | ONE_TOUCH | NO_TOUCH | AMERICAN (an option on
                                                       -- a future: AMERICAN, or VANILLA for European)
);

trades (
  trade_id        TEXT PRIMARY KEY,   -- blotter 'Trade Id' column, or app-generated 'MANUAL-<n>'
  source          TEXT NOT NULL,      -- 'XLSX' = every blotter-sourced trade (a historical literal, kept so
                                      -- existing databases are unaffected) | 'MANUAL' = booked on the Manual entry sub-tab
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  product         TEXT NOT NULL,      -- FX_SPOT | FX_FWD | FX_SWAP (booked manually, data/ingest/manual.py::book_fx_swap)
                                      -- | FUTURE | FX_OPTION (dormant, kept) | EQ_OPTION (the generic listed-option P&L
                                      -- path, no ingest) | CMDTY_OPTION (an option on a commodity future) | LME_FWD
  package_id      TEXT NOT NULL,      -- = trade_id, except the two trades of a manual FX swap: 'SWAP-' || min(trade_id)
  trade_date      TEXT NOT NULL,
  quantity        REAL NOT NULL,      -- signed, in base_ccy units: base amount (FX), contracts (FUTURE, CMDTY_OPTION),
                                      -- tonnes (LME_FWD), notional (FX_OPTION: + = long)
  price           REAL NOT NULL,      -- fill: forward outright / future or option price as quoted / USD per tonne
                                      -- (LME_FWD) / FX option premium per unit
  account         TEXT NOT NULL,
  counterparty    TEXT NOT NULL,
  strategy        TEXT NOT NULL,      -- '' for blotter trades (the export has no such column)
  trader          TEXT NOT NULL,
  description     TEXT NOT NULL,      -- raw description or free text
  theme           TEXT NOT NULL DEFAULT ''   -- bundle membership, see instrument_theme
);

instrument_theme (                     -- theme = bundle name; membership of a Blotter bundle
  instrument_id   TEXT PRIMARY KEY REFERENCES instruments,
  theme           TEXT NOT NULL
);

bundles (                              -- a bundle's own metadata only, never its membership
  name            TEXT PRIMARY KEY,
  description     TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

trade_legs (
  trade_id        TEXT NOT NULL REFERENCES trades,
  leg_no          INTEGER NOT NULL,
  leg_type        TEXT NOT NULL,      -- FX_NEAR | FX_FAR | NOTIONAL
  ccy             TEXT NOT NULL,
  amount          REAL NOT NULL,      -- + = receive / long, − = pay / short
  start_date      TEXT NOT NULL,      -- trade_date
  settle_date     TEXT NOT NULL,      -- value date / futures expiry
  rate            REAL NOT NULL,      -- fill for FX legs and futures; 0 for an option's NOTIONAL
  settles_cash    INTEGER NOT NULL,   -- 1 for FX legs and an LME ticket's USD leg; 0 for NOTIONAL legs and an
                                      -- LME ticket's metal leg
  PRIMARY KEY (trade_id, leg_no)
);

marks (
  as_of_date      TEXT NOT NULL,
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  settle_date     TEXT NOT NULL,      -- outright date; = as_of_date for SPOT; expiry for futures
  mark_type       TEXT NOT NULL,      -- SPOT | FWD_OUTRIGHT | FUTURE_PX
                                      -- | PREMIUM | DELTA | GAMMA | THETA | VEGA | RHO
                                      -- | DELTA_PA (premium-adjusted delta, written only for G10 pairs quoted
                                      --   that way; the ladder reads DELTA, never DELTA_PA)
  value           REAL NOT NULL,      -- DELTA = base-ccy delta per 1 unit of trades.quantity (may exceed 1 for digitals)
  source          TEXT NOT NULL,      -- BBG_BFXFORWARD | BBG_BDH | BBG_BDP | BBG_INTERP | QL_OPTIONS_PRICER | MANUAL
  snapped_at      TEXT NOT NULL,      -- ISO timestamp, offset resolved from America/New_York for that row
  PRIMARY KEY (as_of_date, instrument_id, settle_date, mark_type, source)
);

curves (                               -- OIS discount curve nodes (engine/rates::bootstrap_and_store writes them)
  curve_id        TEXT NOT NULL,      -- 'USD-SOFR-OIS', ...
  as_of_date      TEXT NOT NULL,
  node_date       TEXT NOT NULL,
  discount_factor REAL NOT NULL,
  par_rate        REAL NOT NULL,      -- 0 where the node carries no par rate
  source          TEXT NOT NULL,      -- BBG_BDP | BBG_BDH | MANUAL | QL_PRICER (bootstrap output, engine/rates)
  PRIMARY KEY (curve_id, as_of_date, node_date, source)
);

curve_quotes (                         -- raw OIS quote staging (bbg-curves writes; engine/options/rates.py builds its
                                       -- discount curves from them, the FX options' only rates input)
  as_of_date      TEXT NOT NULL,
  ccy             TEXT NOT NULL,
  index           TEXT NOT NULL,      -- 'SOFR', 'ESTR', 'SONIA', 'TONA', 'SARON', 'CORRA', 'AONIA'
  tenor           TEXT NOT NULL,
  ticker          TEXT NOT NULL,
  value           REAL NOT NULL,
  quote_type      TEXT NOT NULL,
  field           TEXT NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, index, tenor, source)
);

realised_pnl (                         -- one frozen row per settled trade, written by engine/pnl/ledger.realise_settled;
                                       -- a SETTLED row in value_book comes from here and is never recomputed
  trade_id            TEXT PRIMARY KEY REFERENCES trades,
  instrument_id       TEXT NOT NULL,
  product             TEXT NOT NULL DEFAULT 'FX_FWD',
  currency            TEXT NOT NULL,   -- the quote currency a future was frozen in (CNY for SHFE copper)
  settle_date         TEXT NOT NULL,
  local_amount        REAL NOT NULL,
  usd_entry_amount    REAL NOT NULL,   -- 0.0 = not applicable (crosses), not "zero P&L"; a future's is
                                       -- contracts × multiplier × fill × S (S = the last official conversion spot on or before its expiry, 1 for USD)
  mark_type           TEXT NOT NULL DEFAULT 'SPOT',   -- which mark_type froze this row (SPOT, FUTURE_PX, PREMIUM, CLOSE_OUT)
  spot_usd_per_local  REAL NOT NULL,
  spot_as_of_date     TEXT NOT NULL,   -- date of the mark used to freeze (settle date, or last prior)
  spot_source         TEXT NOT NULL,
  pnl_usd             REAL NOT NULL,
  frozen_at           TEXT NOT NULL,
  note                TEXT NOT NULL    -- '' or 'spot dated <d> (last before settlement)'; a non-USD future adds the
                                       -- conversion it was frozen at
);
```

```sql
bbg_library (                          -- what the trades on file need from Bloomberg for their P&L (see "Bloomberg library")
  trade_id        TEXT NOT NULL,      -- no foreign key: an upload rewrites the whole book before the sync runs
  kind            TEXT NOT NULL,      -- SPOT | FWD_OUTRIGHT | FUTURE_PX | CONTRACT_DATES | OIS_CURVE | VOL_SMILE | LME_CURVE
  key             TEXT NOT NULL,      -- pair / future instrument_id; currency for OIS_CURVE
  settle_date     TEXT NOT NULL,      -- FWD_OUTRIGHT, FUTURE_PX: the date marked; '9999-12-31' otherwise
  bbg_ticker      TEXT NOT NULL,      -- the security asked for; '' where the kind stands for a set of them, or where a
                                      -- future's root has no Bloomberg ticker (listed, never requested)
  role            TEXT NOT NULL,      -- PAIR | CONVERSION (a USD-conversion pair's SPOT) | UNDERLYING (an option's future)
  product         TEXT NOT NULL,
  needed_from     TEXT NOT NULL,      -- the trade's trade_date
  needed_until    TEXT NOT NULL,      -- settle date / expiry: not asked for after it
  added_at        TEXT NOT NULL,
  PRIMARY KEY (trade_id, kind, key, settle_date)
);

bbg_library_state (                    -- one row; dirty = 1 when the trades changed since the last sync
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  dirty           INTEGER NOT NULL DEFAULT 1,
  synced_at       TEXT NOT NULL DEFAULT ''
);
```

Views: `marks_official` (below) and `trades_official` (a passthrough of `trades`, the name every engine query reads). Both are dropped and recreated on every startup by `create_schema`, never `CREATE VIEW IF NOT EXISTS`, so a view-definition change always reaches an existing database.

Lanes keep tables of their own, created defensively (`CREATE TABLE IF NOT EXISTS`) in their own modules: `contract_static` (contract-master: Bloomberg's last trade and first notice dates per commodity contract), `vol_quotes` (FX smiles), `manual_rates` / manual vols (engine/options), `spread_overrides` (spreads-engine: a trade pinned into or split out of a spread; read, with no screen that writes it yet).

`data/ingest/schema.py::purge_retired_sources` runs once per startup and removes what retired sources left in an old database: `source = 'BNP'` trades, `BNP_BVAL` / `WORKBOOK_REFERENCE` marks, the `positions` table, and (since 2026-09-24) the `swap_review` table. It never deletes `BBG_INTERP`. The macro products that left on 2026-09-24 (rate swaps, NDFs, the FX-swap package rule, the equity index) leave harmless leftovers on an old database: `index_fixings`, `irs_direction_overrides`, `instrument_rate_options` / `rate_vols` / `rate_model_params`, marks of the retired types (never official any more), and frozen IRS / NDF_FIX rows the ledger keeps as they are. A new database creates none of them, and the first upload of Jason's blotter replaces every non-MANUAL trade.

Leg layouts: FX spot / forward = 2 legs (`FX_NEAR`, one per currency); FX swap = two FX_SWAP trades sharing `package_id`, the near trade with 2 `FX_NEAR` legs and the far trade with 2 `FX_FAR` legs, booked by `data/ingest/manual.py::book_fx_swap` (the engine values each as a forward on its own date); FUTURE = 1 `NOTIONAL` leg in the contract's quote currency, amount `contracts × multiplier × fill`, `settle_date` = expiry, `settles_cash` 0; FX_OPTION = 1 `NOTIONAL` leg in base ccy, `settles_cash` 0; CMDTY_OPTION = 1 `NOTIONAL` leg in the contract's quote currency, amount `lots × multiplier × fill`, `settle_date` = the option's expiry, `settles_cash` 0; LME_FWD = 2 `FX_NEAR` legs dated the prompt, the metal leg (`ccy` = the root id, amount = tonnes, `settles_cash` 0) and the USD leg (`−tonnes × fill`, `settles_cash` 1), so only its USD cash reaches the ladder.

### Official marks

`marks.source` is part of the primary key, so joining `marks` directly returns one row per source and double-counts as soon as two sources exist for the same date. Exactly one source is official per `mark_type`:

| mark_type | official source |
|---|---|
| SPOT | BBG_BFXFORWARD (an LME metal's cash price included, from its cash ticker, keyed `settle_date = as_of_date`) |
| FWD_OUTRIGHT | BBG_BFXFORWARD where Bloomberg quotes that exact date (a standard tenor of `FWD_CURVE`); otherwise BBG_INTERP (user decision 2026-09-18): linear interpolation between the two bracketing standard-tenor outrights of Bloomberg's own curve, never extrapolated beyond the last tenor. A direct quote always wins over an interpolated row for the same key. An LME metal's curve follows the same rule (`data/bloomberg/fwd_curve.py::lme_curve_marks` / `lme_history_marks`): its 3M and monthly pillars are BBG_BFXFORWARD at Bloomberg's own prompt date, BBG_INTERP on a computed date (every past close, which carries no date), and a ticket's broken prompt is BBG_INTERP between the pillars, never beyond the last |
| FUTURE_PX | BBG_BDH (a future's price, commodity futures included; also Bloomberg's own price of a listed option (an option on a commodity future), written on the option's instrument at its expiry date, its mid on a live pull; on a past close the daily `PX_LAST` for both, user decisions 2026-09-22 and 2026-09-24) |
| DELTA, DELTA_PA, PREMIUM, GAMMA, THETA, VEGA, RHO | QL_OPTIONS_PRICER (`engine/options`, vendored options_calc) |

Never official, reconciliation only: `BBG_INTERP` for anything but the FWD_OUTRIGHT fallback (a SPOT or FUTURE_PX is never interpolated into existence); `MANUAL` for option premiums and Greeks; any `BNP_BVAL` row surviving in an old database (nothing writes one any more). The mark types of the products that left on 2026-09-24 (PAR_RATE, PV_USD, DV01_USD, CASHFLOW_USD, NDF_1M, NDF_FIX) are official under no source, so an old database's rows of them never reach `marks_official`.

The mapping is the `marks_official` view: `marks` filtered to the official source per `mark_type`, plus the FWD_OUTRIGHT fallback row only where no direct-quote row exists for the same key (`data/ingest/schema.py::OFFICIAL_MARK_SOURCE` and `OFFICIAL_FALLBACK_SOURCE`), so `(as_of_date, instrument_id, settle_date, mark_type)` is unique.

Standard-tenor forward points are official. The live pull (`data/bloomberg/live.py`) requests Bloomberg's bulk `FWD_CURVE` table once per pair, for every pair with an open FX leg and every open FX option's underlying pair, and writes each standard-tenor row as an official `BBG_BFXFORWARD` `FWD_OUTRIGHT` at that tenor's own settle date, because each row is Bloomberg's own outright quote. A leg or option expiry that falls between two tenors is written as `BBG_INTERP` (the API exposes no direct broken-date outright, `docs/open-questions.md` item 27); that is how a broken-date leg gets its P&L at all. P&L reads forwards by exact leg date, so the tenor rows change nothing there; they exist so that `engine/options/rates.py`'s covered-interest-parity fallback has official points to interpolate between when a currency has no OIS curve. The pull reports them under `curve_points_written`, separately from the requested-mark count.

Past closes come from the backfill (`data/bloomberg/backfill.py`), which runs straight after every requested pull, the header's reference dates first; the same button press does both and no screen runs it separately.
- **FX:** a past FX close is Bloomberg's 15:00 New York value (see "Mark time"), read from its intraday bars (`pull_marks.fetch_intraday_close_series`: the BID and ASK bar ending 15:00, mid of the two closes, one request per ticker and side per stretch) for every day that history reaches (user decision 2026-09-22: "for fx use new 3pm"). A day the intraday history no longer reaches is asked of the daily history (`PX_LAST`, one request per stretch).
- **Futures and listed options:** the daily `PX_LAST`. A commodity future's history is asked under the name valid on the day of the request (`data.contracts.request_ticker`: the two-digit year once expired), and written on its own instrument and expiry. An option on a future likewise (`data.contracts.option_request_ticker`), with its underlying future's close, and its Greeks are re-priced for that day from that day's prices only (`engine/options/store.py::price_close`).
- **LME:** a past LME close is each curve pillar ticker's daily `PX_LAST` (cash, 3M, the monthlies up to the furthest open prompt), stamped 17:00 New York like futures (a low-stakes choice taken 2026-09-24); `backfill.is_close_row` takes the instrument id and applies that rule to an LME root's SPOT and FWD_OUTRIGHT.
- **FX options:** after a day's closes are written, the backfill asks Bloomberg's daily history (PX_LAST, one request per kind per stretch) for the vol-smile quotes of every pair with an FX option open that day, and the OIS quotes of every currency an open FX option needs that day. It writes them into `vol_quotes` / `curve_quotes` dated that day under BBG_BDH; a day that already holds them is not asked again. It then prices the FX options open and not closed out that day (`engine/options/store.py::price_close`) from that day's own inputs, never another day's, so a past close has its own PREMIUM and Greeks. An option with an input missing that day is skipped with its reason. The backfill's status block for these inputs is `inputs`.
- **Past forward curves:** Bloomberg serves no historical `FWD_CURVE`, so a past day's curve is built from the standard-tenor tickers' history, converted from points to an outright with Bloomberg's own divisor (`FWD_POINTS_SCALE`, or else 10 ** `FWD_SCALE`, never a hard-coded pip size). A converted forward more than 20 % from that day's spot is not written.
- **Tenor dates:** a tenor's value date is Bloomberg's own `SETTLE_DT` when history returns one. Otherwise it is computed by market convention from that day's spot date (T+2, T+1 for USDCAD / USDTRY / USDPHP / USDRUB; `config/holidays.txt` is the calendar).
- **Interpolated rows:** every forward built from points or placed on a computed date is written as `BBG_INTERP`, never `BBG_BFXFORWARD` (user decision 2026-09-21). Nothing is extrapolated beyond the last tenor.
- **Scope of the asks:** the backfill asks Bloomberg's history only for what the book needed, read from the Bloomberg library (user decision 2026-09-21): the days being worked, as stretches of consecutive business days; the pairs and futures needed inside each stretch; and per pair the standard tenors from SP up to the one that clears its furthest open leg by a week.
- **On request only:** Bloomberg is pulled only when asked (hard rule 8). `live.LiveFeed` sleeps until "Pull Bloomberg now" and then runs one cycle, today's marks and then the backfill. `live.INTERVAL_SECONDS` is only the screens' own re-read of the marks on file.

### Marks snapshot

`data/bbg_snapshot/` (`data/bloomberg/snapshot.py`) is how the marks reach a PC without a Terminal: the database is git-ignored, so nothing Bloomberg wrote is in git otherwise.
- **After a pull:** every "Pull Bloomberg now" ends with the export (`snapshot.save_after_pull`, run by `backfill.start_auto_backfill` after the backfill of a real pull only, off under `RISK_SNAPSHOT=0`). The files are left in the working copy for the user's own commit and push; the outcome is one line in the status file's backfill block, never a failure of the pull.
- **By hand:** `2_launcher.py marks-export` is the export plus a commit of `data/bbg_snapshot/` alone (`--push` also pushes).
- **What the export writes:** every table a pull writes, `snapshot.MARKET_TABLES` = `marks` (whole, every source), `curves`, `curve_quotes`, `vol_quotes` and `contract_static` (Bloomberg's commodity contract dates). It also writes the `instruments` rows the marks hang off, each table's DDL and the last pull's status JSON, as CSV / JSON in primary-key order.
- **Import:** `marks-import` on the other PC makes its market data what the Bloomberg PC had at the export. In every table, rows of any source but `MANUAL` are dropped first, and a local instrument is never overwritten. Then `data/ingest/contract_dates.py::apply_contract_dates` moves this PC's commodity futures to Bloomberg's contract dates, and `realise_settled` runs. The import refuses on a PC that has Bloomberg unless `--force`. An older snapshot carrying tables the app no longer has loads without them.
- **Trades never travel:** no trade travels (hard rule 1), and no trade file is ever committed (user, 2026-09-22: "i dont want to commit any trade files").
- **Contents:** the folder was emptied on 2026-09-24, when the macro trader's marks left; the next real pull on the Bloomberg PC writes Jason's.

### Bloomberg library

`bbg_library` (`data/bloomberg/library.py`) is the one record of what the trades on file need from Bloomberg for their P&L. The live pull, the curves step, the vol step, the backfill and the Market data tab's "needed" lists all read it, so what is not in it is never asked for.

- **FX spot / forward / swap:** the pair's SPOT until the last leg settles, and a FWD_OUTRIGHT at each leg's own date. For a cross, also the SPOT of each currency's USD pair.
- **Commodity futures** (a future whose `base_ccy` is a contract root in `config/contracts.csv`):
  - FUTURE_PX until expiry, asked under `data.contracts.request_ticker`: the one-digit year while live, the canonical two-digit id once expired.
  - A non-USD future or listed option also needs the SPOT of its currency's USD pair (role CONVERSION), from trade date to expiry.
  - Until Bloomberg's own dates are stored (`contract_static`), it needs `CONTRACT_DATES` (FUT_LAST_TRADE_DT, FUT_NOTICE_FIRST), for today's pull only. This is checked at read time, so a stored date stops the ask without a resync.
  - A pull asks the dates first and stores them. `data/ingest/contract_dates.py::apply_contract_dates` then moves the instrument's expiry, its NOTIONAL legs and its FUTURE_PX mark keys to Bloomberg's date (values untouched: the one write to `marks` outside the Bloomberg lanes), before any futures price is asked.
  - A future whose root has no Bloomberg ticker is listed with `requestable` False and its reason, and is never asked. `needed_on` / `needed_in_range` leave such rows out unless `include_unrequestable=True`.
- **Option on a future** (CMDTY_OPTION), until expiry: FUTURE_PX on its own ticker (role PAIR, its mid on a live pull; all its P&L needs), the CONVERSION SPOT when non-USD, and for its Greeks on every day it is open the underlying future's FUTURE_PX (role UNDERLYING, also for an underlying with no trade) and one OIS_CURVE (its currency's where `engine/options/rates.py` has one, else USD); its CONTRACT_DATES (`OPT_EXPIRE_DT`, else `LAST_TRADEABLE_DT`) for today's pull only, until stored.
- **LME forward** (LME_FWD), until its prompt: the metal's SPOT under its cash ticker, a FWD_OUTRIGHT at the prompt (ticker '', a curve read) and `LME_CURVE` (key = the root id, standing for `engine.lme.lme_curve_tickers`' pillars, trimmed to cash, 3M and the monthlies up to the first past the furthest open prompt). The live pull asks them in its own LME step (`live._lme_step`, `status["lme"]`), never in the FX requests; `needed_in_range` leaves LME rows out unless `include_lme=True`. An LME curve is complete on a day when its cash and 3M are official.
- **FX option,** until expiry: the pair's SPOT and its USD-conversion SPOTs, the OIS curve of both currencies and the pair's vol smile on every day it is open, and for today's pricing only a FWD_OUTRIGHT at the expiry.
- **When a need ends:** nothing is asked for after `needed_until`. An option the ledger has realised is left out of a live pull.
- **When it changes:** only when the trades change, or when the code's list of needs does.
  - Triggers on `trades` / `trade_legs` set `bbg_library_state.dirty`.
  - An upload syncs it at once and says so in its summary.
  - Any reader finding it dirty, or stamped by another `library.LIBRARY_VERSION`, syncs it before reading.
  - A pull never writes it.
- **On screen:** the Market data tab lists it ("Bloomberg library": ticker, field, what it is for, how many trades, until when), with non-requestable needs shown as gaps.

### Bloomberg check (`py 2_launcher.py bbg-check`)

The contract universe's Bloomberg roots and price scales are best guesses until a terminal confirms them. Only 11 of 202 roots are verified, and 102 were filled on 2026-09-24 as stand-ins (user: "make the code with the tickers you think are best - and add a bloomberg diagnostic tool I will be able to use"). `data/bloomberg/ticker_check.py` is that tool, run on request at the Bloomberg PC (hard rule 8).
- **Root check:** it asks each root's generic front ticker ('CL1 Comdty') for NAME, EXCH_CODE, CRNCY, FUT_CONT_SIZE, FUT_VAL_PT, tick size and value, and prices. It compares them with `config/contracts.csv`. Bloomberg's 'USd' / 'GBp' currency, or a value per point 100× off our multiplier, is a SCALE_MISMATCH with the price scale that would agree.
- **Book check:** with a database, it compares the book's own fills with Bloomberg's prices and stored contract dates, and asks the conversion spots.
- **Search:** it can search `//blp/instruments` for a root Bloomberg does not know.
- **Reports:** it writes a plain-English report and a fixes worksheet under `reports/`.
- **Applying fixes:** `py 2_launcher.py contracts-apply <worksheet>` applies the rows marked `yes`, through `data.contracts.apply_fixes`. That refuses a stale or invalid row and never writes a file that would not load.
- **LME and options** (2026-09-24; `--lme` / `--options` run only these, a plain run includes them): each LME metal's cash, 3M and first monthly ticker (price, currency, Bloomberg's prompt date against ours), and per root with an `option_style` the option chain Bloomberg lists against contract-master's ticker form, exercise style and lead months (worksheet rows for `option_style` and `option_lead_months`; findings that are code are listed "For the housekeeper").
- **Scope:** it never writes marks or trades. The procedure is in `docs/bloomberg-pc-checklist.md`.

### Blotter → tables

`data/ingest/blotter.py` parses the transaction-level blotter export: one row per fill, with a genuine per-fill `Price` and `Trade Id` for every product. The reference shape is the synthetic Jason book `data/sample/blotter_sample.csv`, which follows the prime-broker export's column layout. A real commodity export has not been seen yet; the user parked that on 2026-09-24. Shared dataclasses, regexes and helpers live in `data/ingest/common.py`.

**Scope.** A row is excluded only when:
- its `Status` says cancelled / rejected / pending / void / deleted / failed; or
- a populated `Fund`, `Trader` or `Desk` cell is outside the lists in `config/book.yaml`. An empty list takes every value, and a missing column or blank cell never excludes.

**Row kind** is decided by `Fin Type`, matched by keyword after normalisation, with `Product` as the fallback: `FORWARD | CURRENCY | FUTURE | OPTION`. A row of a product that left the app (a rate swap, an equity-index future, a listed index option) is counted and skipped with a plain reason, shown under "NOT LOADED". Anything else unrecognised is counted and skipped, never coerced.

**Tolerance rule.** A blank, missing or oddly formatted field never rejects a row when the value can be recovered from another column: forwards fall back from the Description to `TradeDate` / `Settle Date` / `Buy Currency` / `Sell Currency` / `Price`; options to `Currency Pair` / `Adj. Expiry Date` / `FxOption Type`; futures rebuild Quantity or Price from `NetInvoice`. Only a contradiction between two populated fields rejects.
- A repeated `Trade Id` within a file keeps the highest `Version`.
- File reading (`blotter.read_table`) accepts UTF-8 / BOM / cp1252, comma / semicolon / tab / pipe delimiters, a header row after preamble lines, any header casing, and multi-sheet workbooks with real Excel date cells.
- Parser guesses are listed in `docs/blotter-parser-assumptions.md`.

- FORWARD: `Symbol` is `<PAIR><VD mmddyy>-<id>`; that trailing id is the `Instrument Id`, not the trade id, which comes from the `Trade Id` column. Base / quote leg amounts come from the structured `Buy Currency` / `Sell Currency` / `BuyCurrency Amount` / `SellCurrency Amount` columns, cross-checked against the description's sold / bought currencies. The blotter's Buy / Sell is our side. Every pair is deliverable (`is_ndf` 0, both legs `settles_cash` 1).
- CURRENCY: spot FX fills. A row naming both a `Buy Currency` and a `Sell Currency` is written as product `FX_SPOT` with the same two `FX_NEAR` legs a forward gets, dated on `Settle Date`. A single-currency row (fee, balance, one-sided movement) writes only its `CASH-<ccy>` instrument, never a trade, never a reject.
- FUTURE: a commodity future, resolved through the contract universe (`data.contracts.resolve_future`).
  - It accepts the prime-broker form 'CLZ6-USAA', Bloomberg forms, Chinese 'CU2611' / ZCE 'SR611', and an explicit 'SHFE:CU2611' prefix.
  - A code two exchanges share is told apart by the `Currency` cell and `Execution Venue`. An ambiguous or unknown root rejects, naming the candidates; a multiplier is never guessed.
  - One trade + 1 `NOTIONAL` leg per fill in the contract's currency. `Quantity` is contracts signed by `Side`; `multiplier`, currency and Bloomberg ticker come from `config/contracts.csv`.
- OPTION: product `FX_OPTION`, 1 `NOTIONAL` leg in the pair's base currency (`Currency Pair` column), quantity signed by `Side` (Buy = long = +), price = premium fill as a fraction of base notional. Terms the export leaves blank are typed once on the Blotter's Manual entry sub-tab and stored in `instrument_options`. An option on a commodity future (a listed `ROOT/[EA]yymmdd[CP]strike` Symbol, a Bloomberg option form, a Chinese option code or an exchange prefix, with no currency pair) is product `CMDTY_OPTION`, resolved through `data.contracts.resolve_option`: quantity = lots signed by `Side`, price = the premium as quoted; its underlying future's instrument row is written too. An equity-index option is counted and skipped as a retired product.
- LME: a FUTURE or FORWARD row naming an LME metal (Bloomberg's cash or 3M ticker, a root of `engine.lme.lme_roots()`, or venue / description LME with a metal) is product `LME_FWD`, never a FUTURE: quantity = lots × the lot's tonnes, price = USD per tonne, the prompt from a prompt or maturity column, the description, the settle date, or for a 3M ticket its 3M date (said in the load report); a prompt that is not a valid LME prompt is a warning, never a reject. The LME ferrous contracts stay FUTUREs.

`trades.strategy` is `''` for blotter trades.

### Upload and manual entry

- **An upload replaces the whole book** (user decision 2026-09-17: "when a new excel is put in - that's the only input for the trades - all of the old stuff gets deleted").
  - `data/ingest/upload.py::import_blotter` deletes every non-MANUAL trade and its trade-keyed rows (`trade_legs`, `realised_pnl`) inside the same transaction that publishes the new file.
  - It does so only after the new file has parsed, so a parse failure leaves the existing book intact.
  - Instruments, marks and curves are untouched.
  - Bloomberg's stored contract dates are applied to the new futures and options on futures (`apply_contract_dates`, which moves every mark keyed on the old expiry, values untouched).
  - The summary counts loaded trades by kind in plain words (futures, options on futures, LME forwards, FX forwards, FX spot, FX options), names the book filter's exclusions and the underlying futures written with no trade.
- **Manual entry** (`data/ingest/manual.py`, Blotter sub-tab) books OTC trades the export does not carry, as `source = 'MANUAL'`, ids `MANUAL-<n>`.
  - It writes the same instrument / trade / leg shape the parser writes for that product, so every engine query sees them like any other trade.
  - It covers FX forwards, FX options, and FX swaps (`book_fx_swap`, a choice on the screen since 2026-09-24).
  - A MANUAL trade survives every upload and leaves only through `delete_manual_trade`, which removes both trades of a swap.
- An upload or a manual trade asks nothing of Bloomberg. The upload brings the Bloomberg library up to date and its summary says how many tickers the new book needs; the next "Pull Bloomberg now" prices it.
- The launcher's sample import runs only on an empty database.
- After an upload or a marks write the UI refreshes in place, with no browser reload (`ui/revision.py`).

### Tabs as views

Seven tabs under a header that sits above all of them (`ui/app.py::VISIBLE_TABS`), in this order: Blotter, Ladder, Curve, Spreads, Expiries, Risk, Market data (Curve, Spreads and Expiries added 2026-09-24 for the commodity book, after the Ladder so Blotter stays first and the Ladder second; earlier: user, 2026-09-22: "I want the first tab to be blotter, and the second to be cash ladder"; the Risk tab came the same day, user: "I want to create a new tab to see all these metrics, as a book, and as per underlyers"; the app opens on the Blotter). Every tab is a read-only view: `ui/` never recomputes P&L or delta. The header and the ladder's risk table are always the whole book; filters shape only the view they sit on.

**Header (all tabs).** LTD, Daily, Previous day, 5d, MTD, YTD and Trading P&L, the trade count (open / settled), Net and Gross USD delta, and a collapsible LTD line chart over every business day from the book's first trade date to the as-of date (user, 2026-09-22: "I want to see the ltd line chart, which requires all the previous closes"; it showed the last 20 business days before), one valuation per day, memoised on the database's mtime and built only when the chart is opened. Its as-of is today in New York by default (user, 2026-09-22: "by default, always price pnl as of today, so that the top bar numbers all reflect todays numbers, unless changed specifically otherwise"): the layout is built on every page load so the default is never frozen at start-up, the Blotter's and the Ladder's date pickers feed it when the user changes one (last change wins; picking today itself, the Today buttons, is not a departure), and after 17:00 New York, the FX day roll (user, 2026-09-22: "only roll to new day after new york 5pm", then "all date rollover at hkt 5am"; `data/bloomberg/live.py::book_today`, `ROLLOVER_HOUR_NY`, the one day boundary of the app, see "Mark time"; `cash_ladder.today_ny` returns it), the header and both pickers roll to the new day unless another day was picked (`ui/app.py`, `header.as_of_after_pick` / `as_of_after_tick`). Marks keep the New York calendar date (`live.book_today`); a manual trade is dated the calendar day it was dealt. A day with no marks yet is valued off the near marks (hard rule 2), so today's figures are the latest closes carried forward until the day's pull. Aggregation: a figure sums the priced trades only and says so in a visible caption ("excludes N of M trades unpriced", breakdown by product and reason on hover); a period difference leaves out any trade priced on only one of its two dates; and when the trades unpriced on the reference date outnumber those priced at both ends, that close is not usable. A period whose reference close is not usable steps its reference date back one business day at a time to the first earlier close that has value, at most 5 business days (user, 2026-09-21: "let it backfill up to 5 days"), and names the date it used in its caption (user decision 2026-09-21: "use previous date until has value"); only when none of those has value is the period n/a, with a sentence naming the reference date. **The fill** (user decision 2026-09-21: "there should be a fill when bloomberg doesnt have the data", after "how is that possible given the fill function??" and "ive told you like 5 times about the fill function"): on any date a screen values, the date looked at as much as a reference close, a trade with no price takes its own valuation from the last earlier business day on which it has one, at most 5 back (`engine/pnl/reference.py::fill_book`, applied once in the reader every screen shares, `ui/tabs/blotter_pricing.py::priced_value_book`). The row keeps what it is on that date and takes the earlier close's mark, spot and P&L whole, never a mix of two dates; its note opens "no price on <date>: value of the <earlier> close" followed by why it had none, the LTD card and each period card say how many trades were filled and back to which close, with each trade's note on hover, and the chart's hover says so per day. A filled value is never carried further (each date is filled from unfilled valuations), a trade with no earlier price in reach stays blank with its reason, and a stored value that is not a number is never filled. Nothing is written to `marks`: the Market data tab still shows the mark as missing, and the ladder's delta is not filled. The engine's own `value_book` and `ledger.ltd` stay unfilled (NaN if any row is NaN); the fill and the partial sum are the screens' rule, never a change to a trade's P&L formula. Since 2026-09-22 the near-marks rule of hard rule 2 runs first, inside `value_book`, so the fill only reaches a trade with no mark of its kind on any date. The trade count and Net / Gross USD delta are shown even when no P&L mark exists. No figure is ever blank without its reason. **Commodity strip** (2026-09-24), after the FX Net / Gross: gross commodity notional and net outright by sector (`book_positions(...)["commodities"]`), open spreads and groups for review (`engine.spreads.book_spreads`, memoised on the database's mtime and the as-of, since it values the P&L periods), and the next first notice or last trade (`engine.expiry.expiry_schedule`'s first row, coloured by its level).

**Ladder.** "What am I long or short, and when is it cash?"
- Layout (user decisions 2026-09-21): one table, one row per currency: FX rate as quoted, Local delta and USD delta first, then Settled cash, the dates across and the total of the columns shown, with the USD equivalent (and the net USD delta) as its bottom row. The rate / delta figures were first a table of their own above the grid; the user found that clunky and odd to scroll. A missing or refused rate is named in a caption, never in a repeated "Rate source" row.
- Grid: `trade_legs` where `settles_cash = 1 AND settle_date ≥ as_of`, grouped by `ccy, settle_date`, leg by leg, crosses included. No P&L on it.
- `≥` here versus `>` in the delta query is intentional: a leg settling on `as_of` is cash that moves today but carries no delta by close.
- **Settled cash row**, on top (user decision 2026-09-18: "expired tickets must settle not disappear"; `engine/ladder/exposure_adapter.py::settled_records_from_db` is its only source). Deliverable legs past their value date sit there at face value in their own currency and still carry that currency's delta (CNH received on a settled forward is CNH exposure until sold). Futures, options on futures and FX options contribute their USD settlement read from `realised_pnl`, never recomputed; one the ledger has not realised yet is named under the grid, not valued. This is settled cash from the tickets on file, not a bank balance.
- The tab's per-currency delta therefore includes settled deliverable cash, and deliverable legs settling on `as_of` count as cash by close. The SQL delta query below and the per-pair Position table stay open-forward-only. Delta rows are at official spot. Gold keeps its own sign in the dollar-convention column (a metal is not a dollar position). Every FX leg sits on its own value date: NDFs left the app on 2026-09-24.
- **USD equivalent column** (`engine/ladder/usd_marks.py`): each cell at the USD-per-unit rate for its own value date: spot on or before the pair's spot date (the app's one rule, `engine/pnl/calendar.py::spot_date`: T+2 weekdays, T+1 for USDCAD / USDTRY / USDPHP / USDRUB, rolled forward off a `config/holidays.txt` holiday; user yes 2026-09-22, reviewer finding), otherwise the official `FWD_OUTRIGHT` for that exact date, else linear interpolation between the bracketing official outrights with spot as the first pillar, flat beyond the last tenor, and spot when a pair has no forward curve on file (named in a caption, never silent). Undiscounted; settled cash at spot. Its total is the book's FX value at outrights, which is not the headline P&L (that converts quote P&L at spot).
- View controls (currency multiselect, From / To value dates, "Settled dates one by one", "Show table in USD equivalent", CSV downloads, the heatmap, "Local vs USD by value date") shape the grid only.
- Stress block per `docs/BUILD_PLAN.md` section 4 (`config/stress.yaml`, currency moves with CNH among them). The open futures table shows each contract's price in its own currency, its conversion and its USD delta; commodity futures carry no FX scenario figure (commodity scenarios are on the Risk tab) and are not in the headline card or the risk table's Net / Gross, which are FX only (2026-09-24). An LME ticket's USD leg sits on the grid at its prompt and in Settled cash after it; its metal leg is never a currency (the metal is on the Curve tab). An open option on a future is no currency exposure.

**Blotter.** "Where did the P&L come from?" `value_book(as_of)` rows, one per trade, open or settled. Sub-tabs: Total book, FX, Futures & LME, Options, Bundles, Manual entry (the Rates sub-tab left with the rate swaps on 2026-09-24).
- Total book: above the P&L-by-asset-class table (FX, Futures, LME forwards, Options with the options on futures; a leftover product on an old database is counted under "Other" with its reason, never dropped), the **Positions** table, the key table of the Blotter (user, 2026-09-22: "this is the key table of the blotter"; `engine/ladder/positions.py::book_positions`, rendered by `ui/tabs/blotter.py::positions_table`): one row per currency with delta, the Ladder's own risk table (rate as quoted at official spot, local delta, USD delta, FX options' delta included, a metal row marked as not in the FX net, largest |USD delta| first, USD last), then FX net USD delta (the header's number, + = long USD) and gross, then FX options delta (USD) by pair. Above those, first, the **Commodities** section (`book_positions(...)["commodities"]`, from curve-positions): one line per sector (net and gross USD) with its commodities under it (exchange, net and gross lots, net units, net and gross USD), a Commodities total marked as not in the FX net, and the P&L the non-USD futures and options hold in each currency. Every figure is the module that owns it summed, nothing recomputed; a missing mark leaves its line "n/a" with the reason on hover and out of the sum, never zero.
- FX: FX trades × `marks_official` (`FWD_OUTRIGHT` at the leg's own `settle_date`, `SPOT` for USD conversion); per-pair USD notional = Σ sign(base leg) × |USD leg|. Above the trade table sit two P&L-by-currency tables (user decision 2026-09-21): a fixed one for the whole FX book (LTD, Daily, Previous day, 5d, MTD, YTD per currency, its total equal to the strip) and one summed over the rows the trade table currently shows. Currency = the pair's non-USD currency; a cross is listed under its pair name. Sums follow the header's display rule. A trade the engine could not price shows "n/a" in its mark and P&L cells with its reason on hover, never a made-up figure (the illustrative "(sample)" cells of 2026-09-17 were removed on 2026-09-22, reviewer finding, user yes). Both tables show every currency at full length, with no scroll box of their own.
- Futures & LME: grouped by sector, then commodity (an LME ticket under its metal), each trade with its exchange (contract-master), product, quantity with its unit (lots, or tonnes for LME), fill, mark, and its P&L in the contract's own currency (`pnl_local`, with the currency beside it) and in USD, as `value_book` gives them, with USD subtotals per commodity and sector (known figures summed, "excludes N" otherwise); a settled future shows its frozen USD P&L (the ledger stores USD only). The OIS discount curves stay (`engine/rates`: `bootstrap_and_store`, flat forwards, log-linear in the discount factor; a bootstrap that does not converge raises, never a fallback), read by the option pricers only; `py 2_launcher.py reprice` re-prices the options from the marks on file, day by day, asking Bloomberg nothing.
- Options: not a top-level tab. A grouped, collapsible trade summary in the user's Bloomberg MARS-style layout: Portfolio Totals, then asset class, then structure / `package_id` (a multi-leg package collapses to one summary row with its legs nested underneath); columns Position / Notional / MktVal / MktPx / Delta / Theta / Gamma / Vega / Expiry / Underlying / UndFwdPx / Rho, with Side / Type / Payoff / Strike right after the label, ahead of them (user decision 2026-09-21: Strike, Type and Payoff are typed in the table on an option's own row, so they must be in sight without scrolling; the table's refresh is held while one of those cells is selected). Data: FX_OPTION and CMDTY_OPTION trades × `marks_official`. Options on futures form their own group, "Options on futures" (grouped by root in the four tables above): lots, Bloomberg's price, the value at lots × multiplier × price, the notional on the underlying future's price, the Greeks from the option marks (DELTA in futures lots, the others in the contract's currency; a group sums a Greek only within one unit), and their terms read-only, from the symbol. The four tables above the trade summary (By pair, Expiry ladder, By structure, Spot against strike) are built from live options only: a trade the book values with status CLOSED (see "A closed-out option is not live") is left out before grouping, told by that status and never by its marks or a zero value (user, 2026-09-22: "I only want to see live options, no need show closed out options"); their Total line and Portfolio Totals still carry the closed-out group's P&L, and the trade summary still lists it, labelled "(closed out)", at its closing fill with no Greeks. Phased build-out in `engine/options/__init__.py`'s scope ledger and `docs/open-questions.md` item 61.
- Bundles: named groups of trades; membership is `instrument_theme` / `trades.theme`, metadata in `bundles`.

**Curve** (commodity conversion, 2026-09-24). "What am I long or short, in which month?" `engine/curve::curve_positions`, rendered by `ui/tabs/curve.py`: one row per commodity (grouped by sector), contract months across, each cell the net position in lots, physical units or USD (a switch), then net and gross lots, units and USD; sector totals with a Book line; one row per contract (expiry marked "(est.)" until Bloomberg's date is stored); the P&L the non-USD futures hold in each currency; flat contracts collapsed. Prices and spots are the exact official marks of the day, never estimated: a contract with no price or spot keeps its lots and units and shows its USD as n/a with the reason. Two delta views (2026-09-24): an averaging contract's delta shrinks through its pricing month (business days left over the period's, `data.contracts.averaging_period`), an option on a future counts its DELTA mark in futures lots under its underlying's month, an LME ticket its tonnes over the lot size under its prompt's month; the lots, units and USD views are futures and LME only.

**Spreads** (commodity conversion, 2026-09-24). "Where is my relative-value P&L?" `engine.spreads.book_spreads(conn, as_of)`, rendered by `ui/tabs/spreads.py` through the shared filled reader. The grouping rule (`engine/spreads/__init__.py`): a user bundle first; else a calendar (two months of one root, opposite signs, lots within 5 %); else a `config/spreads/` template whose legs fit its weights within 5 %, on one account and trade date, except that a template spanning two currencies (China against the West) may take its legs from two accounts of the book; a trade in one spread only; an ambiguous or near-miss group goes to review, never guessed. Each open spread shows its size, legs, LTD / Daily / 5d / MTD / YTD (its legs' `value_book` figures summed against the header's reference closes; n/a with the leg's reason when a leg is unpriced) and its leftover outright; then the closed spreads, the outrights and the groups for review.

**Expiries** (commodity conversion, 2026-09-24). "What is about to expire or go to delivery?" `engine/expiry::expiry_schedule`, rendered by `ui/tabs/expiries.py`: every open commodity contract's next event (first notice for a physical or unknown-delivery contract when Bloomberg's date is on file, else last trade; last trade for a cash-settled one), its alert date and the business days to it on the contract's own exchange calendar, and a level (EXPIRED, RED at 3 business days or fewer, AMBER at 10 or fewer, GREEN; `engine/expiry/levels.py`). While a physical contract's dates are estimated its alert date is held early, at the first business day of the month before the contract month, and every estimated date is shown as estimated. An option on a future alerts on its own expiry (held early to its lead month while estimated; a physical underlying's delivery named); an LME ticket alerts on the day its prompt becomes the cash date (the prompt less 2 LME business days). A contract the ledger has frozen leaves the alerts for a collapsed "Expired and settled" list (2026-09-24).

**Risk.** "How much can the book lose?" The risk metrics of the user's nm-dashboard project (its `fx_alpha` package: `core/risk.py` and the Portfolio tab of `dashboard.py`), computed here for this book (user, 2026-09-22: "on another project one level up, called nm-dashboard, we have lots of risk metrics, including blended vol, stress, 1y 95var etc. I want to create a new tab to see all these metrics, as a book, and as per underlyers"): `engine/risk/metrics.py::book_risk(conn, as_of)`, rendered by `ui/tabs/risk.py`, which recomputes nothing.
- Positions are the book's own: one underlyer per currency (USD delta, FX options' delta included) and per metal from `engine/ladder/positions.py::book_positions`, and one COMMODITY underlyer per contract root from curve-positions' delta lots (`engine/risk/commodity.py`: an option at its delta on its underlying's history, an averaging contract at its shrinking delta, an LME ticket on the research app's contract for its prompt month). SECTOR and SPREAD rows are views of those parts, shown apart and never added to the Book, so nothing counts twice. The engine takes no delta of its own and reads no mark.
- The return history is the nm-dashboard's Bloomberg cache, read from disk (`engine/risk/history.py`: `RISK_HISTORY_DIR`, else the freshest of `../nm-dashboard/fx_alpha/bbg_data`, `../nm-dashboard/bbg_data` and `../bbg_data/bbg_data` by the last close in its `bbg_raw_fx_marks.parquet`; the tab names the folder, its last close and the copies it saw): daily closes as USD per unit since 2000 and deposit yields for carry. `engine/risk/commodity_history.py` reads the research app's settlement history per contract (`COMMODITY_HISTORY_DB`, else `../Commodity Dashboard/var/rv.sqlite`, read-only): a position's daily USD P&L is its own contract's settlement changes × lots × our multiplier (before the contract listed, the constant-maturity series at its depth), converted at the research app's FX (CNY through USDCNH). On this PC it is mock data from 2020-09-21. It serves the risk metrics only: nothing from it is written to `marks` or used for P&L or delta (hard rule 2 untouched), and it asks Bloomberg nothing (hard rule 8). With no folder found every figure is "n/a" with the reason; this PC has none, and no pyarrow either.
- Definitions, the dashboard's exactly (`config/risk.yaml` holds the parameters, defaults equal to the dashboard's constants). Daily $ P&L per underlyer, the book held constant across the history: `usd_delta × (Δln close + carry)`, carry `(yield_ccy − yield_USD)[t−1] / 100 / 365`. `lag2` = the last close at or before as-of, less 2 business days. Blended annual vol = 2/3 × std of the last 500 daily P&Ls × √252 + 1/3 × std over 2008-01-01..2010-12-31 × √252 (trailing alone before 2011-01-01), on the series to `lag2`, n/a under 500 observations. 1y 95 % VaR = −(5th percentile of the last 252 daily P&Ls), positive = a typical bad day, n/a under 252. Worst day raw = the minimum daily P&L over all history. Worst day ex shocks = the minimum from 2008-01-01 to `lag2` with the shock dates zeroed (SNB 2015-01-15, Brexit 2016-06-24): the stress basis, against a cap of `stress_pct` (50 %) of the vol target. The book's figures are those functions of the summed series, so correlation is in them; per-underlyer figures are standalone. Net and Gross USD = the currency and metal rows' USD delta summed, and their absolute values summed; the commodity net and gross are curve-positions' delta figures, shown apart. The commodity history does not reach the 2008-2010 crisis window, so such a row takes the trailing vol alone and says "crisis window not in history: trailing vol only" (a documented choice, 2026-09-24). The shock dates include 2020-04-20 / 21 (negative WTI) and 2022-03-07 / 08 (LME nickel), one list for every row.
- Limits: `vol_target_usd` (4.5m, the macro fund's figure, still to be replaced by Jason's: `docs/open-questions.md` C5) and the stress cap; the tab shows blended vol as % of target and the worst day ex shocks as % of cap, flagged when over. Scenario stress reuses `engine/pnl/stress.py` on the same delta, the Ladder's scenarios (`config/stress.yaml`), nothing new computed. **Commodity scenarios** (`engine/stress/`, `config/commodity_stress.yaml`, 17 placeholder scenarios until Jason's): outright moves by sector, subsector, exchange or root (the most specific wins), curve steepen and flatten, spread blow-outs (crack, crush, China against the West), CNY and MYR moves on the currency exposure, and historical replays through the research history (n/a with the reason where it does not reach); each position's P&L is its delta USD × the move (first order: an option's gamma is not in it), attributed to the book's spreads. **Margin and limits** (`engine/limits/`, `config/limits.yaml`): an initial-margin estimate per root × month at placeholder rates with spread credits, always labelled "estimate, not exchange SPAN", and the desk and exchange position limits, every one NOT_SET until the user fills it in.
- Layout: a caption (as-of, history folder and dates, config), the book's cards, the per-underlyer table with the Book pinned last (rows by gross USD), the scenario table with its currency matrix, and a definitions block. No date picker of its own: it follows the header's as-of.

**Market data.** "Can I trust the numbers?" Organised by currency pair: spot and the forward curve with each mark's source and snap time, official first; the feed status carries the OIS curves block (`status["curves"]`), the contract-dates summary (`status["contract_dates"]`) and the futures never requested for want of a ticker (`status["not_requestable"]`), and a Contract dates panel lists each commodity contract's Bloomberg dates or that they are missing; a Futures curves section shows each commodity root with an open position (every contract on file, its official FUTURE_PX with source and snap time, the previous close and the change, a missing price with its reason); the feed status carries the LME step (`status["lme"]`) and the options-on-futures line; "Pull now" (the same action as the top bar's "Pull Bloomberg now": one feed cycle, today's marks, the backfill and the marks snapshot; user 2026-09-22) and feed status; on a PC without a terminal that same press re-prices the options from the marks on file instead (user, 2026-09-22: "pull bbg now should recalc options too, using log data if no bbg access, or pull new data for new calculation"; `engine/options/store.py::recalc_on_file`, run by `live.pull_once` when no session opens: every day from the first option trade to the book date that has inputs on file, each from its own day's marks, then the book date at the pricing time; the status file's `recalc` block and `recalc_summary` sentence, shown on the button's status line and in the feed status; it asks Bloomberg nothing, hard rule 8); the feed status also says what the ledger's re-freeze did (user yes, 2026-09-22): the pull's `ledger` block and the backfill's `ledger` block, one shape (`live.ledger_block`: `refrozen` as the ledger's own `{trade_id, product, mark_type, spot_as_of_date, pnl_from, pnl_to, why}`, `kept` as its `{trade_id, product, reason}`, `refrozen_count`, and the one sentence `refrozen_summary`, "N settled trade(s) re-frozen at the close"), each trade's P&L before and after and why in a collapsed list; close completeness for the trailing business days; the Bloomberg library; the manual mark-entry form; the Bloomberg connection check.

### Delta per currency

Aggregate delta per currency across forwards, futures and option deltas in one query:

```sql
WITH d AS (
  SELECT l.ccy, l.amount AS delta
  FROM trade_legs l JOIN trades t USING (trade_id)
  WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP','FUTURE') AND l.settle_date > :as_of
  UNION ALL
  SELECT i.base_ccy, t.quantity * m.value
  FROM trades t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
  UNION ALL
  SELECT i.quote_ccy, -t.quantity * m.value * s.value
  FROM trades t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  LEFT JOIN marks_official s ON s.instrument_id = i.base_ccy || i.quote_ccy AND s.mark_type = 'SPOT'  AND s.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
)
SELECT ccy, SUM(delta) AS delta FROM d GROUP BY ccy;
```

The `SPOT` join in the option quote-currency branch is a `LEFT JOIN` so that a missing spot mark surfaces as a `NULL` delta; the engine must raise if any resulting delta is `NULL`, never drop the leg silently. The join key is the **pair** (`i.base_ccy || i.quote_ccy`, e.g. `USDJPY`), not the option's own `instrument_id` (e.g. `USDJPY111926P-197571137`), which can never match a SPOT row. Because SQL `SUM()` drops individual NULL rows inside a `GROUP BY` group, the engine checks for options with a DELTA mark but no pair SPOT *before* aggregating (`engine/ladder/ladder.py::_OPTION_MISSING_SPOT_SQL`) rather than inspecting the summed output.

Per-pair delta (the "Position" table) is the same union grouped by `t.instrument_id` in USD-notional terms.

## P&L conventions

- **Sign**: `quantity > 0` = long base currency / receive; P&L is positive when the mark moves in favour of the position. Long USDXXX profits when the pair rises; long XXXUSD profits when the pair rises.
- **Display notional**: USD notional per pair, sign = direction of the base currency. Storage keeps exact leg amounts.
- **Source of truth**: fills (`trades` + `trade_legs`). Marks come from Bloomberg (`BFXFORWARD` / `BDH` / `BDP`) and from the app's own pricers on Bloomberg inputs.
- **Mark date**: each FX leg is marked with the `FWD_OUTRIGHT` for its own `settle_date` (not a single T+5 date); with none on file for that date, the near-marks estimate of hard rule 2 (along the day's curve, so a metal with spot alone on file is marked at spot: user decisions 2026-09-21 and 2026-09-22, "xauusd has no fwd outright"). Every FX forward is deliverable: the NDF fixing rule left with the NDFs on 2026-09-24. A future is marked at its own contract's `FUTURE_PX`, keyed on its expiry.
- **USD conversion**: quote-currency P&L converts to USD at **spot** of the same `as_of_date`, never at the forward outright.
- **Options on commodity futures** (user decision 2026-09-24; Phase 5): product `CMDTY_OPTION`, `PnL_local = contracts × multiplier × (m − f)` with `m` Bloomberg's own price of the option (official `FUTURE_PX` on the option's instrument) and `multiplier` its underlying future's; `PnL_USD = PnL_local × S` at spot; frozen after expiry at the last official price on or before it, converted at the expiry date's spot. No model enters the P&L; Greeks are Black-76 (European) or Barone-Adesi-Whaley (American) on the underlying future's price at the vol the option's price implies (`engine/options/equity_commodity.py::price_listed_commodity_option`, written under QL_OPTIONS_PRICER by `engine/options/store.py`'s bulk passes; DELTA in futures lots per option lot; a currency with no OIS set-up discounts on USD SOFR, named). A bought-and-sold-back option needs no close-out rule: Bloomberg's price cancels over the flat pair.
- **LME forwards** (user decision 2026-09-24; Phase 5): product `LME_FWD`, the FX-forward rule: `PnL_USD = tonnes × (m − f)`, `m` the official outright for the ticket's own prompt date (the near-marks rule along the day's LME curve: cash, 3M and the monthly prompts), USD-quoted so `S = 1`; frozen at the prompt date at the last official cash price on or before it; the cash lands on the ladder on the prompt date. The day's LME curve puts its cash pillar at the LME cash date (`engine.lme.cash_date`, T+2 LME business days), not the FX spot date, and the freeze reads `engine.lme.settlement_price`, so the provisional and frozen rows agree to the cent.
- **Per-trade LTD P&L (USD)**, with `Q` = base amount, `f` = fill, `m` = outright mark for the leg's value date, `S` = spot (quote→USD):
  - FX, any pair: `PnL_quote = Q × (m − f)`; `PnL_USD = PnL_quote × S` (`S = 1` when quote is USD). Crosses: `S` = USD per quote unit from that currency's own USD pair; never invent a USD leg.
  - Futures: `PnL_local = contracts × multiplier × (m − f)` in the contract's quote currency, `multiplier` = quote-currency amount per 1.0 of quoted price per contract (contract size in the quote's unit × price scale: 50 USD per cent on CBOT corn, 300 GBP per penny on ICE NBP, 400 USD per 1.00 on CME lean hogs, 40,000 lb quoted per hundredweight; `data/contracts/` computes it and refuses a `config/contracts.csv` row that disagrees); `PnL_USD = PnL_local × S`, `S` = USD per quote unit at spot of the same `as_of_date` (`S = 1` for USD contracts; user decision 2026-09-24, "Spot of valuation date", the FX rule above applied to futures, so SHFE / DCE / ZCE / INE / GFEX in CNY, OSE in JPY, Euronext and TTF in EUR, NBP in GBP are never summed as dollars). A settled future freezes at the last official price on or before expiry, converted at the spot of its expiry date (the last official spot on or before expiry, as FX; user decision 2026-09-24).
  - Listed option (EQ_OPTION, the generic listed path kept for Phase 5's options on commodity futures; user decision 2026-09-21: "Bloomberg's option price"): the futures formula (P&L in its quote currency, converted at spot), with `m` Bloomberg's own price of the option (official `FUTURE_PX` on the option's instrument: its mid on a live pull, else last; `PX_LAST` on a past close, user decision 2026-09-22) and `f` the fill. No model enters the P&L. Frozen after expiry at the last official price on or before it, like a future. Its Greeks come from `engine/options/equity_commodity.py` (Black-76 / American on the underlying future's price, the vol that the option's price implies); a missing input blanks the Greeks with its reason, never the P&L. Options on commodity futures are booked as CMDTY_OPTION on this same path (user decision 2026-09-24).
  - FX option: `PnL_USD = quantity × (PREMIUM_mark − premium_fill) × S`, premium in base-ccy fraction, `S` = USD per base unit at spot; realised at expiry at the last official PREMIUM on or before expiry (since 2026-09-18, user-approved, that PREMIUM is the expiry-day payoff: on the expiry date `engine/options` writes the payoff at the pair's official SPOT of that date — call max(S−K,0)/S, put max(K−S,0)/S, a BASE-payout digital 1 in the money else 0, nothing at S = K — following the official SPOT on file for that date until the first freeze; if the app did not run that day it is written on a later pull, dated the expiry date; the ledger freezes the trade the day after expiry; the payoff is taken at the day's official spot, not at the option's cut time. Digitals pay the BASE currency: USD on USDJPY, EUR on EURSEK, confirmed by the user). Before expiry a digital's PREMIUM is priced on the smile (user decision 2026-09-21: "yes, price off the smile"): a tight call / put spread, each leg at its own smile vol, so the slope of the smile is in the price; with no smile on file (an ATM-interpolated or a manual vol) it is the single-vol closed form.
- **Settled trades are frozen.** A trade whose last leg has settled takes its row from `realised_pnl` and is never marked again; settlement does not move LTD. The freeze is at the pair's last official SPOT on or before the settlement date (a future or a listed option: its last official FUTURE_PX on or before expiry, converted at the last official spot on or before its expiry, never an estimate; an LME ticket: the metal's last official cash price on or before its prompt, S = 1; an FX option: its last PREMIUM, or CLOSE_OUT for a closed-out group). A settled trade the ledger has not frozen yet (every one after an upload, which clears `realised_pnl`) is shown by `valuation._frozen_row` at the same figure the ledger will freeze. **Re-frozen at the close** (user decision 2026-09-22): on every call `realise_settled` compares each frozen row with what the official marks on file now give for the same trade by the standard rule (`engine/pnl/ledger.py::purge_superseded`: mark type, mark date and every stored figure) and, where they differ, drops the row and freezes it again in the same call: a trade frozen at a live press once the backfill lands that day's 15:00 close, a future or listed option frozen at a live price once that day's close replaces it, a non-USD future once its conversion spot changes (a row frozen under the older price-date rule is re-frozen at the expiry-date spot and reported), an option frozen at a PREMIUM once its close-out is recognised. A row whose inputs did not change is untouched and keeps its `frozen_at`; only rows whose value date is before the call's as-of are looked at; a row the rule cannot recompute today keeps its figure; each re-freeze is reported with the figure before and after and one sentence naming what changed (`refrozen`), and a row the rule could not recompute is named under `kept`, never dropped (reviewer findings, user yes 2026-09-22). Rows frozen under the retired IRS and NDF rules on an old database (mark type PV_USD or NDF_FIX, product IRS / SWAPTION / CAP_FLOOR, or an NDF frozen at its fixing date's spot substitute, told by its note) are kept as they are and listed under `kept` until an upload replaces the book (user decision 2026-09-24). A leftover open swap is never frozen: `value_book` shows it as a blank row with its reason, never dropped. The backfill's closing step is the ledger's plain `realise_settled(conn, today)` after the past closes land.
- **A closed-out option is not live** (user, 2026-09-21: "for options closed out theyre not live"). FX option trades of the same option (pair, call / put, payoff, strike, barrier, averaging start, expiry, all on file; the export books the buy and the sell-back under two instrument ids, so never by id) whose quantities net to zero as of the date valued are status `CLOSED`: each is valued at the closing fill (quantity-weighted fill of the side opposite the first trade) instead of a `PREMIUM` mark, `quantity × (closing fill − fill) × S`, so the group needs no mark. The options pricer does not price them either (user, 2026-09-22: "we dont need to price all options, as some of them might be closed out already ... we just present the buy and sell price as pnl"): `engine/options/store.py`'s bulk passes (`price_all_and_store`, its expiry-day catch-up, `price_close`, `recalc_on_file`) leave every trade of a group closed out as of the date priced unpriced, by the same grouping rule imported from `engine/pnl/valuation.py`, old marks on file untouched, and report them under their own head (`PricingOutcome.closed_out`, the dicts' `closed_out` list / count, the pull status's "N closed-out options not priced"), never among the trades that could not be priced. On the close-out date the total is exactly what the marked formula gives, because the mark cancels over a flat position, and from then on it does not move: `S` is frozen too (user, 2026-09-21: "of course you freeze the usd converstion"), the official spot of the close-out date (the last trade's date) or the last one before it, named in the note, never a later one; after expiry the ledger records that same figure for every trade of the group (`realised_pnl.mark_type = 'CLOSE_OUT'`), and a row frozen any other way is frozen again once the close-out spot is on file. An option with a term missing (strike 0) is never matched, and a part sell-back stays live on its marks (`engine/pnl/valuation.py::closed_out_from_rows`).
- **Daily P&L** = `LTD(t) − LTD(t−1bd)`. **Trading P&L** = LTD of trades with `trade_date = t`. **5d P&L** = `LTD(t) − LTD(t−5bd)`. **MTD** = `LTD(t) − LTD(last bd of previous month)`. **YTD** = `LTD(t) − LTD(last bd of previous year)`. All from our own recomputed daily series, never summed day by day; `t−n bd` uses the trading calendar (`config/holidays.txt`). A reference close with no value steps back to the previous business day that has one (user decision 2026-09-21; see "Header"). Past closes are valued at historical Bloomberg marks written by the backfill; the live pull only writes today's.
- **Mark time**: official close is 15:00 `America/New_York` for every day of the book (user decision 2026-09-21: "the EOD is 3pm New York time", which replaced the 17:00 close of 2026-09-15 and at first applied from that day on; user decision 2026-09-22: "for fx use new 3pm" for all the previous closes too, so the LTD chart is on one convention). Bloomberg's daily `PX_LAST` is its 17:00 close, so a past FX close is read from Bloomberg's intraday bars at 15:00 New York and stamped 15:00, and a past-day FX row that carries any other stamp (that day's last live press, or a 17:00 daily close on a day the intraday history reaches) is not a close: the backfill asks for the 15:00 value and replaces it (`backfill.is_close_row`). Only a day that Bloomberg's intraday history (about 140 business days, `backfill.intraday_floor`) no longer reaches takes the daily close stamped 17:00. A future's past close (commodity futures included; user reconfirmed 2026-09-24), and a listed option's, is Bloomberg's daily `PX_LAST` (user, 2026-09-22: "all futures for past date pnl calculation, use px last" and "use px last like all other futures. its a listed option", replacing the same day's earlier `PX_SETTLE` choice, "for futures, can use market close": Bloomberg served no settlement history for the SPX option tickers and the user wants every listed instrument on one field), stamped 17:00 New York (`backfill.settle_stamp`, Bloomberg's daily close); a past day's FUTURE_PX row a live press wrote (PX_LAST or a listed option's PX_MID at the press, stamped at the press) is not a close: the backfill asks that day's daily PX_LAST and replaces it, as it replaces an FX row that is not the 15:00 close (user decision 2026-09-22). The app resolves the offset from the zone per date; intraday = live. Every mark row carries `snapped_at` with the resolved offset for that row. **The book's day turns at 17:00 New York, 05:00 Hong Kong, for everything at once** (user, 2026-09-22: "I want to clarify the time today, so that all daily pnl is calculated from the NY 3pm the day before. I am based in HK, so basically all date rollover at hkt 5am"; `data/bloomberg/live.py::book_today`, `ROLLOVER_HOUR_NY = 17`): the date a pull stamps its marks with, the curves / vol / options steps, the ledger, the backfill's notion of a past day (`is_close_row`, `close_completeness`) and every screen's as-of. A pull at 18:00 New York on D writes D+1's marks stamped at the pull's real time, and the same press's backfill treats D as past, asks its 15:00 bars and replaces D's live rows, so Daily is always the live LTD against the previous day's 15:00 close. Until 2026-09-22 the marks kept the New York calendar date while the screen had rolled, so between 17:00 and midnight New York the screen valued a day with no marks off the previous day's last live press and Daily read 0. A manual trade's default date is still the New York calendar day (`cash_ladder.calendar_today_ny`), an open question.
- **Net USD** (FX only) = the USD position: Σ over pairs of sign × USD notional, sign +1 for USDXXX pairs (long base = long USD), −1 otherwise. The header shows this sign with the word "short USD" / "long USD" underneath. The engine's `portfolio_totals` returns the opposite quantity, the net non-USD delta (+ = long foreign); every view that displays Net USD (the header, the Ladder's headline and risk table) negates it exactly once, itself. **Gross USD** = Σ over pairs of |net USD notional per pair|. Metals are reported separately; commodity futures are positions on the Curve tab, not in Net / Gross USD.
- **Must not replicate** (spreadsheet shortcuts the old Excel calculator used; they guard the live P&L path):
  1. Futures P&L computed as `Q × (m − f) / m`: understates by `f/m`. Always `contracts × multiplier × (m − f)`.
  2. Converting quote-currency P&L at the forward outright instead of spot (≈ 2.3 % error on TRY; also BRL, MXN, IDR).
  3. Marking every pair at one shared date regardless of each leg's own value date, and marking matured trades forever instead of freezing settled trades.
  4. Hard-coded ranges or cell references in place of a real query over `trades` / `trade_legs` / `marks_official`.

## Lanes

Every file has exactly one owning lane, and each lane is one agent, `.claude/agents/<lane>.md` (user decision 2026-09-24, see "Working mode"). ◆ marks the commodity lanes added for "Commodity conversion plan" (user, 2026-09-24); a ◆ lane whose files do not exist yet creates them. The layers run in the order the numbers flow: trades in, market data, pricers, P&L, exposure, risk, screens. "Reads" lists the lanes whose output a lane uses (from the code's own imports and the tables it reads); its consumers are the lanes whose "Reads" names it, which is how the housekeeper finds whom to brief when an interface changes. Paths are relative to the repo root; `tests/` files are named without the folder.

**1 Trades in (`data/ingest/`)**

| Lane | Files | Tests | Reads |
|---|---|---|---|
| ingest-schema | `schema.py`: DDL, migrations, the `marks_official` / `trades_official` views, official sources, retired-source purge | `test_ingest.py`†, `test_trades_official.py` | none |
| ◆ contract-master | `data/contracts/` (the contract universe: symbol → contract, canonical id and Bloomberg ticker, currency, multiplier, units, month cycle, expiry and first notice, Bloomberg's contract dates in its own table), `config/contracts.csv`, `config/spreads/` (spread templates from the research app) | `test_contracts.py` | ingest-schema, exchange-calendars |
| ◆ exchange-calendars | `engine/calendars/` (business days per exchange), `config/calendars/` (one holiday file per exchange) | `test_exchange_calendars.py` | none |
| ingest-parser | `blotter.py`, `common.py` (shared dataclasses and helpers), `config/book.yaml` (the fund, trader and desk codes the book takes), `data/sample/` (the synthetic sample blotters) | `test_blotter.py`, `test_ingest_common.py`, `test_commodity_ingest.py` | ingest-schema, ingest-booking, contract-master, lme-forwards |
| ingest-booking | `upload.py`, `manual.py` (manual forwards, options and FX swaps), `themes.py`, `contract_dates.py` (◆ writes Bloomberg's contract dates onto the futures' `instruments.expiry_date` and legs) | `test_upload.py`, `test_manual.py`, `test_contract_dates.py` | ingest-schema, ingest-parser, bbg-library, options-store, contract-master |

**2 Market data (`data/bloomberg/`)**

| Lane | Files | Tests | Reads |
|---|---|---|---|
| bbg-library | `library.py`, `inventory.py` (what the book needs, what is on file, close completeness) | `test_library.py`, `test_library_commodity.py` | ingest-parser, bbg-live, bbg-curves, bbg-backfill, ladder-grid, contract-master, lme-forwards, pnl-valuation |
| bbg-live | `live.py` (feed, pull cycle: contract dates, prices, OIS curves, vol, options, ledger; `book_today`, status file), `pull_marks.py`, `pull_report.py`, `manual.py`, `marks_csv.py`, `bbg_diagnostics.py` | `test_live.py`, `test_live_contract_dates.py`, `test_bloomberg.py`† | ingest-schema, ingest-parser, bbg-library, bbg-curves, bbg-backfill, rates-pricer, options-store, contract-master, ingest-booking, pnl-ledger, ui-shell |
| bbg-backfill | `backfill.py` (past closes: FX 15:00, futures and listed options PX_LAST; the FX options' vol and OIS history and past-close option pricing) | `test_backfill.py`, `test_auto_backfill.py`, `test_backfill_options.py`, `test_backfill_commodity.py` | ingest-schema, ingest-parser, bbg-library, bbg-live, bbg-curves, bbg-snapshot, options-store, contract-master, pnl-valuation, pnl-ledger, pnl-series, ui-shell |
| bbg-curves | `fwd_curve.py`, `rates_marketdata.py` (OIS quotes), `vol_marketdata.py` (FX smiles) | `test_fwd_curve.py`, `test_bbg_event_loops.py`, `test_bbg_diagnostics.py` | bbg-live, rates-pricer, pnl-valuation, lme-forwards |
| bbg-snapshot | `snapshot.py` (marks export / import) | `test_snapshot.py` | ingest-schema, bbg-live, pnl-ledger |
| ◆ bbg-ticker-check | `ticker_check.py` (the Bloomberg ticker check run at the terminal: each contract root's generic ticker against `config/contracts.csv`, the book's contracts and conversion spots, a worksheet of suggested fixes; `py 2_launcher.py bbg-check`) | `test_ticker_check.py` | contract-master, ingest-schema, bbg-live, bbg-curves, lme-forwards |

**3 Pricers**

| Lane | Files | Tests | Reads |
|---|---|---|---|
| rates-pricer | `engine/rates/` (the OIS discount curves the option pricers read: `bootstrap_and_store`, `build_curve_set`, `snapped_at`; swap pricing left on 2026-09-24) | `test_rates_pricing.py` | bbg-curves |
| fx-options-pricer | `engine/options/` except `store.py` and `equity_commodity.py`: `pricer.py`, `inputs.py`, `rates.py`, `calendars.py`, `structures.py`, `portfolio.py`, `__init__.py` (the scope ledger); `vendor/` is never edited | `test_options_pricing.py`†, `test_options_digital_smile.py` | bbg-curves, rates-pricer, contract-master |
| listed-options-pricer | `engine/options/equity_commodity.py` (listed options on commodity futures: Black-76 / American on the future's price, vol implied by Bloomberg's option price) | `test_listed_options.py` | rates-pricer, fx-options-pricer, contract-master |
| options-store | `engine/options/store.py` (the bulk passes that write PREMIUM and Greeks, expiry payoff, close-out skip, `price_close`, `recalc_on_file`) | `test_options_close.py` | fx-options-pricer, listed-options-pricer, rates-pricer, bbg-curves, pnl-valuation |
| ◆ lme-forwards | `engine/lme/` (LME prompt dates, the forward at a prompt date from the cash / 3M / monthly curve, the prompt-date settlement) | `test_lme.py` | contract-master, exchange-calendars, bbg-curves, pnl-valuation |

**4 P&L (`engine/pnl/`)**

| Lane | Files | Tests | Reads |
|---|---|---|---|
| pnl-valuation | `valuation.py` (`value_book`, near marks, closed-out options), `calendar.py` (business days, `spot_date`) | `test_valuation.py`, `test_calendar.py` | ladder-grid, lme-forwards; the marks of bbg-live, bbg-backfill, options-store |
| pnl-ledger | `ledger.py` (`realise_settled`, re-freeze, `ltd`) | `test_ledger.py` | pnl-valuation, pnl-series, ladder-grid, lme-forwards |
| pnl-series | `reference.py` (periods, reference closes, the fill), `aggregate.py`, `fx_blotter.py` (P&L by currency), `stress.py` | `test_pnl.py`†, `test_aggregate.py` | pnl-valuation |
| ◆ spreads-engine | `engine/spreads/` (spreads found in the book: calendar legs, inter-commodity legs with their ratios; spread-level P&L summed from `value_book` rows, leftover outright; its own tables) | `test_spreads.py` | contract-master, pnl-valuation, pnl-series, pnl-ledger, ingest-booking |

**5 Exposure (`engine/ladder/`)**

| Lane | Files | Tests | Reads |
|---|---|---|---|
| ladder-grid | `ladder.py` (the grid and the delta-per-currency SQL), `views.py`, `exposure_adapter.py` (records, settled cash), `__init__.py` | `test_ladder.py`†, `test_exposure_adapter.py` | ladder-exposure, ingest-parser, bbg-live, options-store, pnl-valuation |
| ladder-exposure | `exposure.py` (`build_exposure`, `portfolio_totals`, USD equivalents), `usd_marks.py` | `test_exposure.py`, `test_usd_marks.py` | ladder-grid, pnl-valuation |
| book-positions | `positions.py` (`book_positions`), `futures_delta.py` | `test_positions.py`, `test_futures_delta.py` | ladder-grid, ladder-exposure, bbg-live, options-store, pnl-valuation, curve-positions |
| ◆ curve-positions | `engine/curve/` (positions by commodity × contract month in lots, physical units and USD; net outright per commodity and sector; the currency exposure of non-USD futures) | `test_curve.py` | contract-master, pnl-valuation, book-positions, lme-forwards, options-store |
| ◆ expiry-monitor | `engine/expiry/` (first notice, last trade, option expiry and prompt dates of the open positions, business days to each on the exchange's calendar, alert levels) | `test_expiry.py` | contract-master, exchange-calendars, curve-positions, lme-forwards, pnl-ledger |

**6 Risk**

| Lane | Files | Tests | Reads |
|---|---|---|---|
| risk-metrics | `engine/risk/` except `history.py` and `commodity_history.py`, `config/risk.yaml` | `test_risk.py` | book-positions, pnl-series, risk-history, curve-positions, spreads-engine, commodity-stress |
| ◆ risk-history | `engine/risk/history.py`, `engine/risk/commodity_history.py` (daily settlement history per contract month, read-only from the research app's database; never a mark) | `test_risk_history.py` | contract-master |
| ◆ commodity-stress | `engine/stress/`, `config/commodity_stress.yaml` (outright, curve-shape, spread, CNH and historical-replay scenarios) | `test_commodity_stress.py` | curve-positions, spreads-engine, risk-history, contract-master |
| ◆ margin-limits | `engine/limits/`, `config/limits.yaml` (initial margin with spread credits, exchange position limits, gross lots) | `test_limits.py` | contract-master, curve-positions, spreads-engine |

**7 Screens (`ui/`; the UI reads the engine's output and never recomputes P&L or delta)**

| Lane | Files | Tests | Reads |
|---|---|---|---|
| ui-shell | `ui/app.py`, `ui/launch.py`, `ui/revision.py`, `ui/uploads.py`, `ui/__init__.py`, `ui/assets/`, `ui/tabs/__init__.py`, `ui/tabs/controls.py`, `ui/tabs/formatting.py`, `ui/tabs/ranking.py`, `ui/tabs/blotter_pricing.py` (`priced_value_book`, the reader every screen shares, which applies the fill) | `test_ui.py`†, `test_app.py`, `test_ui_revision.py`, `test_uploads.py`, `test_launch.py`, `test_ui_ranking.py` | ingest-schema, ingest-booking, bbg-library, bbg-live, pnl-valuation, pnl-ledger, pnl-series, every tab it assembles |
| ui-header | `ui/tabs/header.py` (the strip above every tab, the LTD chart) | `test_header.py` | ui-shell, bbg-live, bbg-library, pnl-valuation, pnl-series, ui-ladder, book-positions, spreads-engine, expiry-monitor |
| ui-blotter | `ui/tabs/blotter.py`: the Blotter tab's frame, the Total book sub-tab (Positions table, P&L by asset class) and the Futures & LME sub-tab | `test_ui_blotter.py`†, `test_ui_blotter_commodity.py` | ui-shell, contract-master, book-positions, pnl-ledger, ingest-booking, and the sub-tab lanes it embeds |
| ui-blotter-fx | `ui/tabs/blotter_fx.py` (the FX sub-tab and its two P&L-by-currency tables) | `test_ui_blotter_fx.py` (new tests) | ui-shell, pnl-series, pnl-ledger, ui-blotter, ui-options |
| ui-bundles | `ui/tabs/blotter_bundles.py` (the Bundles sub-tab) | `test_ui_bundles.py` (new tests) | ui-shell, ingest-booking |
| ui-manual-entry | `ui/tabs/manual_entry.py` (the Manual entry sub-tab) | `test_ui_manual_entry.py` | ingest-booking, ingest-schema, ui-options |
| ui-options | `ui/tabs/options.py` (the Options sub-tab) | `test_ui_options.py` | ui-shell, options-store, fx-options-pricer, pnl-valuation, bbg-live, contract-master |
| ui-ladder | `ui/tabs/cash_ladder.py`, `ui/tabs/exposure.py` (the Ladder tab) | `test_ui_ladder.py`, `test_ui_ladder_view.py` | ui-shell, ladder-grid, ladder-exposure, book-positions, pnl-series, bbg-live |
| ui-risk | `ui/tabs/risk.py` (the Risk tab) | `test_ui_risk.py` | ui-shell, ui-header, risk-metrics, commodity-stress, margin-limits, curve-positions, spreads-engine |
| ui-market-data | `ui/tabs/market_data.py`, `ui/feed_controls.py` (the Market data tab and the "Pull Bloomberg now" control) | `test_ui_market_data.py` | ui-shell, ui-header, bbg-live, bbg-backfill, bbg-library, bbg-diagnostics, pnl-ledger, pnl-series, curve-positions, contract-master |
| ◆ ui-curve | `ui/tabs/curve.py` (the Curve tab: commodity × contract month grid, net outright by commodity and sector, currency exposure) | `test_ui_curve.py` | ui-shell, curve-positions, contract-master |
| ◆ ui-spreads | `ui/tabs/spreads.py` (the Spreads tab: P&L and leftover outright per spread) | `test_ui_spreads.py` | ui-shell, spreads-engine, pnl-series |
| ◆ ui-expiries | `ui/tabs/expiries.py` (the Expiries tab: the roll calendar and its alerts) | `test_ui_expiries.py` | ui-shell, expiry-monitor |

**Across the layers**

| Agent | Owns | Role |
|---|---|---|
| housekeeper | `docs/` | The hub: classifies, routes, briefs, carries every Handoff, runs the full suite, commits and reports. The session runs its procedure by default. |
| reviewer | nothing (read-only) | Reviews P&L arithmetic changes in `engine/` against the conventions and the must-not-replicate list. |
| infra | `1_setup.cmd`, `2_launcher.py`, `3_diagnostic.py`, `requirements.txt`, `pyproject.toml`, `.gitignore`, `.gitattributes`, `.claude/settings.json`, `config/` except the files the lanes above own (`risk.yaml`, `contracts.csv`, `spreads/`, `calendars/`, `book.yaml`, `commodity_stress.yaml`, `limits.yaml`), `tools/`, `tests/conftest.py`, `tests/golden/`, `tests/golden_book.py`, `test_golden_book.py`, `test_health.py`, `test_risk_cli.py`, `test_bloomberg_diagnostic.py` | Clean-up by kind of change, anywhere (see "Working mode"). |
| bbg-diagnostics | nothing (read-only) | Audits every Bloomberg touchpoint; its findings go to bbg-live, bbg-curves or ui-market-data through the housekeeper. |
| explainer | nothing (read-only) | Answers the user's "how does this work" questions while other lanes build. |

CLAUDE.md and `.claude/agents/*.md` change only on the user's say-so, written by the session.

† A shared test file: it still holds older tests of other lanes' modules. A lane edits only the tests of its own modules in it and puts every new test in its own file; the housekeeper never runs two lanes that touch the same shared file at once.

Each agent's memory lives in `.claude/agent-memory/<agent>/`. The lanes split on 2026-09-24 read their predecessor's notes too: `data-ingest` (the ingest lanes), `bbg-data` (the bbg lanes but diagnostics), `options-pricer` (the three option lanes), `pnl-engine` (the P&L lanes), `cash-ladder` (the exposure lanes), `ui-blotter` (ui-blotter-fx, ui-bundles, ui-manual-entry). Those five retired names, and ui-blotter's wider old scope, still appear in `docs/` and in older notes and commits; read them as the lanes above.

Retired on 2026-09-24 with the macro trader's products (CLAUDE.md "Commodity conversion plan", Phase 2): the rates-exotics and ui-rates lanes (their code is deleted); read their names in older notes and commits as history.

## Guard rails learned the hard way

- SQLite stays in `journal_mode=delete`. Do not switch to WAL as a quick fix: in WAL the main file's mtime stops changing on write, which silently breaks every mtime-keyed cache (`ui/revision.py`, the header's LTD cache, the Blotter's pricing cache) and serves stale P&L.
- Writers wait up to 60 s for the lock (`schema.BUSY_TIMEOUT_SECONDS`): an upload holds it for its whole parse and load, and a pull landing meanwhile must not be recorded as a Bloomberg failure.
- Diagnostics pasted by the user come from the Bloomberg PC. The dev `risk.db` has no marks, so "fast" or "blank" locally says nothing about the live app; trace reasons through the code.
- The "Last marks pull" diagnostic fails when the last pull requested 0 marks but the book needs some (a pull pressed before any blotter is uploaded).
