# Bloomberg PC checklist

Use this on the PC that has the Bloomberg Terminal. The app asks Bloomberg nothing until you
press **Pull Bloomberg now** (hard rule 8); between pulls it works on the marks on file.

## Things to check when the Bloomberg connection is here

Everything the app was built without a terminal for, in one list. Most of it is automated:
one command, `py 2_launcher.py bbg-check --search --book --db data\raw\risk.db`, runs the
checks marked **auto** and writes a report and a fixes worksheet under `reports/`
(procedure below). The **manual** ones are a few minutes each. Paste the report's
"For the housekeeper" section back to a Claude session when done.

| # | What | Why it matters | How |
|---|---|---|---|
| 1 | The 202 contract roots: name, exchange, currency, contract size, value per point | Only 11 are verified; a wrong root means no price | auto: root check |
| 2 | Price scales (cents or dollars, pence or pounds) | A wrong scale is a 100x P&L error | auto: root check (SCALE_MISMATCH) |
| 3 | Jason's fills against Bloomberg's price, incl. the broker's $/lb roots (cattle, COMEX copper) | Confirms the broker price conversion | auto: `--book` |
| 4 | Futures close = exchange settlement (PX_LAST against PX_SETTLE, last 5 days) | The app marks at PX_LAST; Chinese exchanges settle at the day's average price | auto: desk check 1 |
| 5 | Open interest and volume: one- or two-sided on SHFE, DCE, INE | Sets the liquidity check's figures for China | auto: desk check 2, then **manual**: compare one SHFE copper contract with SHFE's own daily report |
| 6 | LME open interest and volume per prompt month | Whether the liquidity check means anything for LME tickets | auto: desk check 3 |
| 7 | SGX USD/CNH future: ticker and one year of history | The Risk tab needs it to count the CNY hedges | auto: desk check 4 |
| 8 | Physical or cash delivery per root | Decides whether the app warns at first notice or last trade | auto: desk check 5 |
| 9 | Exchange holidays 2026-2027 (`config/calendars/`, every `# unverified` line, China 2027, GME) | Business-day counts to expiry and the P&L reference dates | auto: desk check 6 |
| 10 | Research history depth: reaches April 2020 (negative WTI) and March 2022 (LME nickel)? | Those stress replays are n/a without it | auto: desk check 7 (no terminal needed) |
| 11 | Bloomberg's contract dates (last trade, first notice) stored for the book's futures | Until then expiries are estimated and flagged early | auto: desk check 8; one Pull Bloomberg now stores them |
| 12 | LME curve tickers and prompt dates (cash, 3M, monthlies) | The LME forwards' marks | auto: `--lme` |
| 13 | The research app on this PC pulls real data (its newest settlement is yesterday's, not mock) | Risk, carry, liquidity and the z-scores all read it | **manual**: open the research app, or read desk check 7's dates |
| 15 | Each exchange's close / settlement time (`close_time` in `config/contracts.csv`, `data.contracts.EXCHANGE_CLOSE`): LME, SGX incl. the USD/CNH future, CME HRC and TIO, ICE Europe per product, HKEX, EEX, GME are best estimates | The "legs closed Nh apart" note and hedge % (2-day moves for legs closing hours apart) read them | **manual**: each exchange's contract specification; the time of PX_LAST's last update |
| 14 | FX forward points divisor and broken-date forwards (`docs/open-questions.md` 27, 28, 71) | Only if Jason books FX forwards (none in his export yet) | auto: `py 2_launcher.py doctor --bloomberg` |

After the fixes are applied: one **Pull Bloomberg now**, then the Data tab's status line
should read marks complete, reference closes complete and contract dates from Bloomberg.

## Real research history (item 13)

The Risk tab's VaR, daily risk, hedge %, best-fit ratio and liquidity, and the Book's z-score,
percentile and carry, all read the research app's database. On the development PC that database
holds generated prices (provider `mock`), so those figures are for layout only and the Risk tab
says "mock history". On the Bloomberg PC:

1. **Load the research app's real history** by its own procedure, `docs/BLOOMBERG_TRIP.md` in the
   research repo (`J.Singh_Commodity_Dashboard`): `setup.bat bloomberg` (never without `bloomberg`:
   it would load mock prices, and a real pull refuses to mix the two), the ticker search and
   check, then `run_pull --dry-run --backfill --sector <s>` and `run_pull --backfill --sector <s>`,
   then `rvapp.quant.engine --mode full`. Jason's book needs three sectors, in this order:
   `metals` (SHFE silver and zinc, COMEX silver and copper, LME copper and zinc), `ferrous` (CME
   HRC, SGX iron ore) and `agriculture` (CME feeder and live cattle); its `fx_daily` (USDCNH) comes
   with the pull. Each backfill stops at the daily cap and resumes where it stopped.
2. **Point the monitor at it.** The monitor looks for `..\Commodity Dashboard\var\rv.sqlite` next
   to its own folder, or the path in `COMMODITY_HISTORY_DB`. The research repo clones as
   `J.Singh_Commodity_Dashboard`, so either clone it into a folder named `Commodity Dashboard`
   beside the monitor, or set the path once:
   `setx COMMODITY_HISTORY_DB "C:\path\to\J.Singh_Commodity_Dashboard\var\rv.sqlite"`
   and open a new Command Prompt before typing `chelsea`.
3. **Check** that the Risk tab no longer says "mock history", and that its price check shows no
   root more than 20 % from Jason's fills.

## Running the check

1. `git pull`, then in the project folder: `py 2_launcher.py bbg-check --dry-run`. It asks
   Bloomberg nothing and prints how many securities and fields each check would ask for. If
   your terminal has a tight data allowance, start smaller in step 2.
2. `py 2_launcher.py bbg-check --limit 5`: a first real run on five roots, to see the
   report's shape.
3. The full run: `py 2_launcher.py bbg-check --search --book --db data\raw\risk.db`.
   `--search` looks up candidate roots on Bloomberg's security search for any root Bloomberg
   does not know; `--book` checks Jason's own contracts; the desk checks run too. To narrow
   it: `--sector energy`, `--root NYMEX:CL --root SHFE:CU`; `--desk`, `--lme` or
   `--options` run only that part.
4. Read `reports/bbg_check_<stamp>.txt`. Each root gets a verdict:
   - OK: name, exchange, currency and value per point all agree.
   - NOT_FOUND: Bloomberg does not know the root (its own words are quoted). The search
     candidates are listed; pick the right one in the worksheet.
   - NO_PRICE: the ticker exists but returned no price (entitlement, or a dead contract).
   - SCALE_MISMATCH: Bloomberg's currency is 'USd' / 'GBp' (cents / pence), or its value per
     point is 100x off our multiplier. The suggested price scale is given.
   - CURRENCY_MISMATCH, EXCHANGE_MISMATCH, NAME_CHECK: read the reason; the last two are
     warnings only.
   The desk checks each say PASS, WARN, FAIL or SKIPPED in one line; the report ends with
   the manual checks and a "For the housekeeper" section.
5. Open `reports/contract_fixes_<stamp>.csv` in Excel. Every suggested change is one row;
   an OK root's "mark as verified" row is pre-filled `apply = yes`, everything else is
   blank. Type `yes` in `apply` for each change you agree with, save as CSV.
6. `py 2_launcher.py contracts-apply reports/contract_fixes_<stamp>.csv --dry-run`, then
   without `--dry-run`. It refuses a row whose "current" value no longer matches the file,
   and never writes a file that would not load.
7. Run `bbg-check` again until only warnings remain, then commit `config/contracts.csv` and
   push, so the other PC gets the fixes.

## Every day

1. Launch with `chelsea`. The browser opens on the Book tab.
2. Upload the blotter ("Upload blotter" in the top bar). An upload is a merge by Trade Id:
   trades already on file are replaced by the file's rows, new ones are added, a cancelled
   row removes its trade. The Blotter tab shows what the upload did.
3. Press **Pull Bloomberg now** (top bar). One press does everything, in order: Bloomberg's
   contract dates for any future that has none on file; today's prices (futures, LME, FX,
   the USD conversion spots); the backfill of the past closes the P&L periods need; the
   marks snapshot for the PC without a terminal.
4. Read the Data tab's status line: marks complete, reference closes complete, contract
   dates from Bloomberg. Anything missing is listed under it with the trades it blocks; the
   Marks check table shows every mark with its source, time and change, flagged rows first.
5. If something stays missing after a second pull, open the Data tab's Diagnostics fold and
   press **Check Bloomberg connection**; a ticker that Bloomberg does not know needs the
   check above.
6. Read the Book's "Needs you": first notice or expiry coming up, missing marks, legs that
   could not be paired, positions large against the market's open interest or volume.

## Moving the marks to the other PC

Each pull saves the snapshot to `data/bbg_snapshot/`. Commit and push it yourself (or run
`py 2_launcher.py marks-export --push`). On the other PC: `git pull`, upload the same
blotter, then `py 2_launcher.py marks-import`.
