# Bloomberg PC checklist

Use this on the PC that has the Bloomberg Terminal. The app asks Bloomberg nothing until you
press **Pull Bloomberg now** (hard rule 8); between pulls it works on the marks on file.

## The first visit: check the contract tickers (once, then after any change to the universe)

The contract universe (`config/contracts.csv`, 202 commodity contract roots) was written
without a terminal. Only 11 Bloomberg roots are verified; 102 were filled on 2026-09-24 as
stand-ins, most of them the exchange code. The price scales (cents or dollars per bushel,
pence or pounds per therm) are unconfirmed too, and a wrong scale is a 100x P&L error. The
Bloomberg check tells you, root by root, what is right and what to change.

1. `git pull`, then in the project folder: `py 2_launcher.py bbg-check --dry-run`. It asks
   Bloomberg nothing and prints how many securities the check would ask for (202 in 5
   requests). If your terminal has a tight data allowance, start smaller in step 2.
2. `py 2_launcher.py bbg-check --limit 5`: a first real run on five roots, to see the
   report's shape.
3. `py 2_launcher.py bbg-check --search`: the full run. `--search` also looks up candidate
   roots, on Bloomberg's security search (the terminal's SECF), for any root Bloomberg does
   not know. To narrow it: `--sector energy`, `--root NYMEX:CL --root SHFE:CU`.
4. Read `reports/bbg_check_<stamp>.txt`. Each root gets a verdict:
   - OK: name, exchange, currency and value per point all agree.
   - NOT_FOUND: Bloomberg does not know the root (its own words are quoted). The search
     candidates are listed; pick the right one in the worksheet.
   - NO_PRICE: the ticker exists but returned no price (entitlement, or a dead contract).
   - SCALE_MISMATCH: Bloomberg's currency is 'USd' / 'GBp' (cents / pence), or its value per
     point (FUT_VAL_PT) is 100x off our multiplier. The suggested price scale is given.
   - CURRENCY_MISMATCH, EXCHANGE_MISMATCH, NAME_CHECK: read the reason; the last two are
     warnings only.
5. Open `reports/contract_fixes_<stamp>.csv` in Excel. Every suggested change is one row;
   an OK root's "mark as verified" row is pre-filled `apply = yes`, everything else is
   blank. Type `yes` in `apply` for each change you agree with, save as CSV.
6. `py 2_launcher.py contracts-apply reports/contract_fixes_<stamp>.csv --dry-run`, then
   without `--dry-run`. It refuses a row whose "current" value no longer matches the file,
   and never writes a file that would not load.
7. Run `bbg-check` again until it exits 0 (or only warnings remain), then commit
   `config/contracts.csv` and push, so the dev PC gets the fixes.
8. Once Jason's blotter is uploaded: `py 2_launcher.py bbg-check --book --db data\raw\risk.db`
   also checks the book's own contracts: each fill against Bloomberg's price (a fill 100x
   off Bloomberg's is flagged), the stored contract dates against Bloomberg's, and the USD
   conversion spots the non-USD futures need.

## Every day

1. Launch with `chelsea`. A browser tab opens on the risk monitor (the Blotter tab).
2. Upload the trade blotter ("Upload trade file") and press Confirm. An upload replaces every
   non-manual trade in the database, so there are never duplicates.
3. Press **Pull Bloomberg now** (top bar, or "Pull now" on the Market data tab). One press
   does everything, in order:
   - Bloomberg's contract dates for any commodity future that has none on file (the
     expiry moves from the estimate to Bloomberg's date);
   - today's prices: FX spot and forward curves, futures, the USD conversion spots;
   - the OIS curves and vol smiles the FX options need, and the options' prices;
   - the backfill of past closes the P&L periods need;
   - the marks snapshot (`data/bbg_snapshot/`), for the PC without a terminal.
4. On the Market data tab, read the feed status:
   - "N contract date(s) stored, M future(s) moved to Bloomberg's expiry": expected on the
     first pulls after an upload.
   - Futures "not requested: no verified Bloomberg ticker": that contract root needs the
     ticker check above.
   - The Bloomberg library lists every ticker the book needs; a gap is flagged with its
     reason.
5. Press **Check Bloomberg connection** (bottom of the Market data tab) and read every line:
   - PASS on "blpapi installed" and "Terminal answers": nothing to do.
   - FAIL on SPOT / FWD_OUTRIGHT / FUTURE_PX coverage: press Pull now again. If it stays
     FAIL, the Terminal may not be logged in, or the ticker is wrong for that instrument:
     run the ticker check.
   - FAIL on OIS curve coverage (FX options): an open FX option needs its currency's OIS
     curve. Press Pull now; if it recurs, the Terminal may lack curve permissions.
   - WARNING "no strike on file" for an FX option: open the Blotter's Manual entry sub-tab
     ("Option terms"), pick the option and type its terms once; they survive every upload.
   - FAIL "Last marks pull ... looks stale": the last pull asked for zero marks but the
     book now needs some. Press Pull now.
6. Check the **Expiries** tab. RED or EXPIRED rows are contracts near first notice or last
   trade. An "(est.)" date means Bloomberg's own date is not on file yet: the level is held
   early on purpose, and the next pull fetches the real date.
7. A trade the export does not carry (an OTC forward, option or FX swap): book it under
   Blotter > Manual entry. It is saved as source MANUAL, priced on the next pull, and never
   removed by an upload.

## Moving the marks to the other PC

Each pull saves the snapshot to `data/bbg_snapshot/`. Commit and push it yourself (or run
`py 2_launcher.py marks-export --push`). On the other PC: `git pull`, upload the same
blotter, then `py 2_launcher.py marks-import`.
