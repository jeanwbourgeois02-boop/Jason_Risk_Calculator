# Phase G build checklist

Every decision from the design conversation of 2026-09-29 (CLAUDE.md "Screens redesign plan -> Phase G"; the design doc https://claude.ai/code/artifact/4e1f8ccf-2cf9-4e78-b059-31829f495f03, tab "Table specs" for column-level detail). Where the conversation changed its mind, the later answer wins. After the build, each line is checked in the running app on the sample book and on Jason's file, and ticked only when seen working; anything not done is listed with its reason.

## Structure
- [ ] Five tabs: Book, P&L, Risk, Blotter, Data; Exposure merged into Risk
- [ ] The trade (PBRoot name) is the unit on Book, P&L and Risk; never split across rows
- [ ] `JSHY10_ZNA1` and `JSHY10.3_ZNA1` show as one trade
- [ ] Every fill lands in exactly one trade; the Book total equals the top bar
- [ ] Leg names carry the exchange ("COMEX Silver Dec26"), never the broker's description
- [ ] One shared valuation per date read by every tab (the lag fix)

## Filter, grouping, headline
- [ ] One filter bar (search, Type, Commodity, Trade, Flags only, Group, Clear) on Book, P&L and Risk
- [ ] Filters keep or drop whole trades only
- [ ] The filter carries across the three tabs
- [ ] A headline above each table, calculated from the rows showing, saying "3 of 6 trades"
- [ ] VaR and daily risk recomputed for the slice, never summed
- [ ] z, hedge % and ratios never totalled; the headline shows the worst
- [ ] The top bar always the whole book

## Book
- [ ] One row per trade: Trade, Type, What it is, Size, Entry, Now, z, Today, Daily, LTD, Next
- [ ] Type by rule; Mixed shown with sub-spreads; type mismatch with the PBRoot flagged (SCO1)
- [ ] Size as the lot ratio (91 : 167); hover: value per side, balance, USD per 1-unit move of the level, Hedge % with correlation
- [ ] Balance by dollar value; unbalanced flagged beyond 10 %
- [ ] China vs West level = converted ratio, China on top; calendars = price difference
- [ ] "Legs closed 12h apart" note on China vs West levels
- [ ] Now's hover on a calendar shows the carry per month
- [ ] z over 1 year of the exact level; percentile and z at entry on hover
- [ ] Today's move coloured by its effect on the trade
- [ ] What it is carries the hedge coverage ("CNH 92 % hedged")
- [ ] Next red / amber only when close; grey ≈ when estimated
- [ ] Flags: unbalanced, hedge oversized (SILARB1), type mismatch, leg without price
- [ ] Stable default order (commodity family, then name); closed trades in a fold
- [ ] Row click: flags line, legs table (hedge last; mark with source, time and previous close on hover), roll-down per leg, carry summed only on one curve
- [ ] Hedge line with coverage in the panel; USD per 1-unit move of the level in the panel
- [ ] Level chart since entry, entry dashed, rolls marked
- [ ] Links: See fills, P&L history, Risk, each filtered to the trade

## P&L
- [ ] Chart: daily bars and cumulative line, following filter and slice
- [ ] Period switch: Today, 5d, MTD, YTD, All, Custom
- [ ] Slice: Trade (default), Commodity, Type; whole trades only
- [ ] Columns P&L, Spread, FX, Hedge, New, Realised, % of total; parts add to the total
- [ ] Total / By month switch
- [ ] Sorted by size of P&L; click for legs (per-leg P&L for the period)
- [ ] Track record folded; exchange slice, top-5 list, carry and rolls sections gone

## Risk
- [ ] One row per trade: Daily risk, Share of book, Hedge %, Ratio his / best-fit, Leftover, FX unhedged, Days to exit
- [ ] Hedge % on 2-day moves for legs closing hours apart
- [ ] Headline: VaR of the rows showing against the vol target, standalone sum beside it
- [ ] Folds: Net by commodity in USD with the month grid on click; Currency; Stress top 5
- [ ] Net in USD by default, physical units on hover

## Blotter
- [ ] Last upload in one line; rows not loaded with plain reasons
- [ ] Every fill in 8 columns with Landed in; unassigned fills pinned and flagged
- [ ] Arriving from See fills filters to the trade
- [ ] Upload history folded

## Data and reliability
- [ ] Status line; problems in plain words with what each blocks
- [ ] Marks check with the four checks: arrived, fresh, sane move, units (engine done: `inventory.mark_checks`)
- [ ] A flagged price stays the official price and is marked on the Book
- [ ] Upload and pull in the background with progress in the top bar (pull progress done: `status["progress"]`)
- [ ] Numbers update in place, no page refresh
- [ ] A bad ticker, file row or section shows its reason while the rest carries on (live pull done; backfill and curves in progress)

## The look
- [ ] One table kit across the app, compact, clear hierarchy
- [ ] Red and amber only for losses and real alerts

## Outside the build
- [ ] Bloomberg ticker check run on the Bloomberg PC before Jason sees numbers (the user)
- [ ] Jason's answers to "Questions for Jason"
