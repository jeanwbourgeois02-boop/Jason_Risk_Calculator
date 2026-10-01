# Jason Risk Monitor: user guide

A short guide to setting up the app, starting it, and what each screen does. For the
technical detail behind the numbers, see [HOW_IT_WORKS.md](HOW_IT_WORKS.md); for
troubleshooting commands, see [README.md](README.md).

---

## 1. What the app is

A risk monitor for Jason's commodity relative-value book: listed futures and their
spreads (calendar, cross-exchange, cross-product, China against the West), options on
futures, LME forwards and FX hedges. Everything is in USD.

It works from two things only:

- **Jason's trade blotter**, uploaded as a file. This is the only way a trade gets into
  the app; nothing is typed in by hand.
- **Bloomberg prices**, pulled only when you press **Pull Bloomberg now**. Nothing is
  pulled automatically.

The unit everywhere is the **trade**: Jason's PBRoot name (for example `COPAR3`), with
every leg and FX hedge under it.

---

## 2. Set up (once per PC)

1. Get `1_setup.cmd` (from the repository, or downloaded on its own).
2. **Double-click it.** It installs Python and Git if they are missing, downloads the
   app into `C:\Users\<you>\Jason Risk Monitor`, installs its packages, creates the
   database, and adds the `chelsea` command to PowerShell.
3. Every step prints `OK` or `FAILED` with the reason. At the end it says
   `Ready` and offers to start the app straight away.

It is safe to run again at any time (new PC, after an update, if something looks
broken). It never touches an existing database.

> If you use VS Code, close it completely and reopen it after setup, otherwise its
> terminal will not know the `chelsea` command.

---

## 3. Launch

Open a **new PowerShell window** and type:

```
chelsea
```

- It first updates the app to the latest code on GitHub, then starts it.
- Your browser opens on the app (usually `http://127.0.0.1:8050`).
- **Keep the PowerShell window open** while you use the app. **Ctrl+C** stops it.
- Typing `chelsea` again while it is running just reopens the browser tab.

If something is wrong, run this from the app folder and follow what it says:

```
py -3 2_launcher.py doctor
```

---

## 4. Daily routine

1. **Start the app** with `chelsea`.
2. **Upload the blotter**: press **Upload blotter** in the top bar and pick the latest
   export from the prime broker.
3. **Pull prices**: on the Bloomberg PC, press **Pull Bloomberg now**. One press fetches
   today's prices and fills in any past closes that are missing. Progress shows in the
   top bar.
4. **Read the header**, then the **Book** tab.

### How an upload works

An upload is a **merge**, not a replacement:

- trades already on file and not in the new file stay as they are;
- a trade the file repeats (same Trade Id) is updated with the file's row;
- a row marked cancelled removes that trade;
- new trades are added.

**Every row loads.** A row the app cannot identify is still loaded, flagged in red as
"Not recognised", so you can see what needs fixing. Nothing is dropped silently.

### When does the day turn?

The book's day turns at **17:00 New York (05:00 Hong Kong)**. Daily P&L is today's live
prices against each instrument's previous official close.

---

## 5. The screen

### The header (on every tab)

One slim row above all tabs:

| Item | What it shows |
|---|---|
| **Daily, MTD, YTD, LTD** | The book's P&L for the day, month, year and since inception. Hover for detail. |
| **Marks chip** | When the latest prices were taken (Hong Kong time). Green "complete", amber when some prices are missing, red when no prices have been pulled yet. |
| **Trade count** | For example "50 trades · 43 open". |

The top bar also holds the tab names, the Bloomberg status dot, and the
**Upload blotter** and **Pull Bloomberg now** buttons.

The header always shows the **whole book** and is never affected by filters.

### Markers you will see

| Marker | Meaning |
|---|---|
| **—** (dash) | The figure could not be worked out. Hover to see why. |
| **Excl. N** | A total that leaves out N trades with no price. Hover for which ones. |
| **Filled N** | N trades had no price that day and use their last earlier close (up to 5 business days back). |
| **Ref 16 Sep** | A period figure measured from an earlier close because the usual one had no prices. |
| **≈ date, grey** | An estimated date (until Bloomberg's own date is on file). |
| **(1,234)** in red | A loss. Money is always shown in full, with losses in brackets. |
| **Data issues (N)** | A collapsed drawer at the bottom of each tab with every reason in one place. |

### Tables

- **Filters live in the column headings**, like a spreadsheet: click a heading to sort,
  click its funnel to filter.
- Filters you set carry across the Book, P&L and Risk tabs.
- A headline above each table sums the rows showing ("3 of 6 trades").
- Hover a section title for its definition.
- **Download CSV** exports the table.

---

## 6. The five tabs

### Book: "What do I hold, and is each trade where I want it?"

The app opens here.

- **Summary card** at the top: P&L today and since entry, broken down by
  **Spread type**, **Commodity family** or **Commodity**. Its total equals the header.
- **Main table**, one block per trade:
  - the trade row (bold) with its spread figures and P&L;
  - each spread under it, with its legs always visible (size, average fill, current
    price, P&L);
  - FX hedges last, in italics.
- **Columns**: Qty, Entry price, Price now, Spread at entry / now (in USD), Ratio at
  entry / now (China leg on top, in USD), Usual ratio (one-year average), Z at entry /
  now, P&L today, P&L since entry. Hover P&L since entry for the open and locked-in
  split.
- **Action chip** next to a trade name, only when something needs doing: a key date
  coming up ("Last trade in 4 days"), no price, price to check, sides out of balance by
  value, a hedge too big or the wrong way, a trade not recognised.
- **Click a trade** for the detail: the flags in words, each leg in USD, roll-down,
  the hedge and its coverage, the spread's history since entry, and links to its fills,
  P&L history and risk.
- **By trade | By contract** switch: By contract shows one row per contract held,
  netted across trades, with clearer, net and gross size and the next key date.
- Closed trades sit in a fold at the bottom.

### P&L: "How did I get here, and what drove it?"

- **One table**, sliced by **Spread** (default), **Strategy**, **Commodity family** or
  **Contract**.
- **One column per period**: Today, 2d, 5d, MTD, YTD, All. Hover a cell to see what
  drove it: spread move, FX, hedge, new trades, realised.
- **Total | By month** switch.
- The headline names the best and worst trade today.
- Click a row to see its legs.
- **Track record** (folded): best and worst day, days up, largest fall, win rate,
  average win and loss, median holding time, by trade type.

### Risk: "How much can I lose, and is each trade really hedged?"

- **One sentence at the top**: "On a bad day (1 in 20) the book can lose about $X",
  with the vol target beside it.
- **One table**, one row per open trade, sorted by risk:
  - **Daily risk**: a typical one-day move in USD;
  - **Share of book**: how much of the book's risk comes from this trade;
  - **Hedged**: Well hedged / Partly / Barely / Adds risk / Outright.
- **Click a trade** for its ratio against the best fit, correlation, leftover exposure,
  FX unhedged, days to exit, z-score and its worst stresses.
- **Worst stresses**: the five worst scenarios, each one click from its per-trade split.
- **Folds**: Net by commodity, Currency (the FX exposure), Option Greeks (only when an
  option is held).
- Risk needs price history: the first **Pull Bloomberg now** fetches about 2.5 years of
  it. Until then the tab says so.

### Blotter: "Did my file load right?"

The audit trail of the uploaded file. Nothing is edited here; a correction is a
re-upload.

- **Last upload** in one line: file, time, new, updated, removed, prices converted, and
  a link to any problems.
- **Fills** sub-tab: every fill with Date, Trade, Contract, Side, Lots, Price (as in the
  file), Trade Id and the trade it landed in. Unrecognised fills are pinned to the top
  in red.
- **Options** sub-tab: type in the option terms the file leaves blank (strike, type,
  payoff), on the option's own row.
- **Upload history** (folded).
- "See fills" from a Book trade opens this tab filtered to that trade.

### Data: "Can I trust today's numbers?"

- **Status line**: last pull, trades, prices complete, reference closes, contract dates.
- **Blotter card**: every row that did not load or needs a fix, with the reason; the
  **duplicates check**; and **Check a blotter file**, which reads a file as an upload
  would and shows what it would do, without saving anything.
- **Bloomberg card**: the last pull, what was already stored and what was new,
  **Pull problems** if any, and folds for missing or flagged prices, the price checks,
  reference closes, contract dates and the Bloomberg ticker check.
- **Diagnosis card**: **Build diagnosis report** writes one plain-text report of
  everything that is wrong, with **Copy to clipboard** and **Download**. Paste it into
  Claude Code to get help.

---

## 7. On a PC without Bloomberg

The database is not shared through GitHub, so a PC without a Bloomberg Terminal has no
prices of its own.

1. On the Bloomberg PC, every **Pull Bloomberg now** saves the prices to
   `data/bbg_snapshot/`. Send them to GitHub with:
   ```
   py -3 2_launcher.py marks-export --push
   ```
2. On the other PC: start the app with `chelsea` (which updates the code), upload the
   same blotter, then run:
   ```
   py -3 2_launcher.py marks-import
   ```

Trades never travel this way: each PC uploads the blotter itself.

---

## 8. Settings Jason can edit

Plain text files in `config/`, each one explains itself:

| File | What it holds |
|---|---|
| `config/risk.yaml` | Vol target, shock days |
| `config/commodity_stress.yaml` | The stress scenarios |
| `config/limits.yaml` | Margin rates, spread credits, position limits |
| `config/book.yaml` | Which fund, trader and desk codes an upload accepts |

---

## 9. Useful commands

Run from the app folder in PowerShell.

| Command | What it does |
|---|---|
| `chelsea` | Update and start the app |
| `py -3 2_launcher.py doctor` | Check everything and say what to fix |
| `py -3 2_launcher.py doctor --bloomberg` | The same, plus Bloomberg tests (Bloomberg PC) |
| `py -3 2_launcher.py start --no-sync` | Start without updating from GitHub |
| `py -3 2_launcher.py bbg-report` | Bloomberg PC: one report of every Bloomberg check, to send back |
| `py -3 2_launcher.py bbg-check` | Bloomberg PC: check every contract's ticker, currency and size |
| `py -3 2_launcher.py marks-export --push` | Bloomberg PC: share the prices through GitHub |
| `py -3 2_launcher.py marks-import` | Other PC: load the shared prices |
