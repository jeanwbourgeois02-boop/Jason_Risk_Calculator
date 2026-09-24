# Blotter parser: what is verified, what is documented, what is a guess

Audit of `data/ingest/blotter.py` against the reference export
`data/raw/new_sample_trades.csv` (857 rows: 743 forward, 85 currency, 11 future,
8 option, 10 IRS; 0 rejects, 0 skipped). Written 2026-09-17.

Status meaning:

- **verified**: the reference file exercises the code path and the result was checked by hand.
- **documented**: the rule is stated in CLAUDE.md but the reference file never exercises it.
- **GUESS**: neither the file nor CLAUDE.md supports it; it is the parser's own assumption.

| Assumption | Evidence on the reference file | Status | Breaks if wrong | What confirms it |
|---|---|---|---|---|
| FORWARD `Symbol` / `Description` regexes are the primary source | 743 of 743 match both | verified | | |
| FORWARD structured-column fallback when Description is blank | never triggered | documented, unverified | silent wrong pair or date on a differently shaped row | a real row with a blank Description |
| `Currency Pair` / `Underlying Symbol` pair fallback with a currency-priority ordering | never triggered | GUESS | wrong base/quote if Symbol and Description both fail | a stated market convention from the desk |
| IRS direction is the sign of `Notional`, not `Side` | `Side` is Buy on all 10 IRS rows; `Notional` always positive | documented; mechanism verified, direction unverifiable from data | wrong pay/receive on every swap | trader or PM confirmation |
| Option Buy = long, Sell = short | 7 Buy, 1 Sell | documented; verified | | |
| IRS `Quantity × 1e6` fallback when `Notional` is blank | never triggered; the 1e6 ratio itself holds on all 10 rows | documented; fallback unverified | notional wrong by six orders of magnitude | a real row with a blank Notional |
| `Fin Type` exact values | 100 % exact: FORWARD, CURRENCY, FUTURE, OPTION, INTEREST_RATE_SWAP | verified | | |
| `Fin Type` fuzzy keywords (Futures, FX Forward, OUTRIGHT, NDF, SPOT, CASH; FX Swap / Currency Swap -> FORWARD, bare Swap -> skipped) | never triggered | three documented in CLAUDE.md, the rest GUESS | a differently worded export misclassified or dropped | an export with different wording |
| `Version` de-duplication keeps the highest version | 0 repeated Trade Ids in 857 rows | documented; unit-tested; unverified on real data | a re-issued trade keeps the wrong version | a real file with a repeated Trade Id |
| Day-first date detection | `TradeDate` values like 20/8/2026 make it unambiguous | verified for this file | wrong dates on a file with no day above 12 | |
| Option expiry and call/put from the Symbol | 8 of 8 match; now cross-checked against the Description | verified | | |
| Option strike from `<n> STRIKE` in the Description | 5 of 8 carry it; the other 3 have no strike anywhere in the row | verified absent (0 sentinel is correct) | | the user types the strike in the Blotter's Options view |
| Futures multiplier table (ES = 50, plus NQ, RTY, YM) | `QtyFactor` / `Factor` blank on all 11 rows | ES verified; NQ, RTY, YM GUESS | P&L wrong by the multiplier ratio on a non-ES future | a real NQ/RTY/YM row or a populated factor column |
| NDF currency list (BRL, TWD, KRW, IDR) | no column indicates NDF-ness | GUESS, inherited from the old BNP parser's provisional list | wrong cash-settlement flag on NDF pairs | prime-broker confirmation of the NDF list |
| CURRENCY row currency from the Symbol | 85 of 85 populated and matching | verified | | |
| CURRENCY row fallback from the `Currency` column | disagreed with Symbol on 85 of 85 rows (it is the other leg) | was a latent bug, fixed 2026-09-17: fallback is now `Buy Currency` on Buy, `Sell Currency` on Sell | | |
| `Side` aliases (b, bought, bot, +, purchase, sold, short, sld, -, sale) | 857 of 857 use exact Buy / Sell | GUESS, unexercised | none today | a file with non-canonical Side text |
| Excluded status words "error" and "draft" beyond CLAUDE.md's list | 100 % Completed | GUESS | could exclude a legitimate status containing those substrings | a file with varied Status values |
| Option `Settle Date` is the premium payment date, not expiry | confirmed against the Symbol-encoded expiry | verified; the code never reads it for expiry | | |

## Fixes made in the same audit

1. The CURRENCY-row fallback picked the other leg's currency (see the table). Fixed.
2. Option expiry parsed from the Description used the file's auto-detected day order
   instead of the fixed US mm/dd/yyyy shape those dates always have. Fixed to match the
   FORWARD path.
3. When both the Symbol and the Description carry a date or a call/put word and they
   disagree, the row is now rejected instead of silently trusting the Symbol.

## Remaining guesses, plain English

1. Which pairs are NDFs. No column says so.
2. Contract size for any future other than ES.
3. The pair-ordering fallback if both Symbol and Currency Pair fail.
4. Version-based de-duplication has never fired on real data.
5. The extra `Fin Type` wording tolerance has never fired on real data.
6. The `Side` alias tolerance has never fired on real data.
7. "error" and "draft" as excluding status words.
8. Which sign of IRS notional means pay fixed, economically.
9. The FORWARD structured-column fallback has never fired on real data.
10. The option free-text expiry fallback has never fired on real data.

Items 1, 2 and 8 are the ones worth confirming with the desk; the rest only matter if a
future export is shaped differently from the reference file.

## Commodity futures (Jason's book, 2026-09-24)

No real commodity export has been seen yet (`docs/open-questions.md` C1). The parser resolves
every non-equity FUTURE row through `data/contracts/` (`resolve_future`); these are its guesses,
each tested against the synthetic sample `data/sample/commodity_blotter_sample.csv`:

1. The prime-broker symbol for a Western commodity future is `<exchange code><month><1-digit
   year>-<4 letters>`, like the ES rows: `CLZ6-USAA`, Brent `BZ6-USAA`, NBP `MX6-GBAA`, TTF
   `TFMX6-EUAA`, OSE gold `JAUZ6-JPAA`, SGX iron ore `FEFF7-USAA`.
2. A Chinese contract comes in its exchange's own form with no suffix: `CU2611`, `I2701`
   (ZCE's three-digit `SR611` is accepted too).
3. The fill is in the contract list's quoted scale (cents for RBOB, heating oil, soybeans,
   soybean oil, corn, copper; pence for NBP). A NetInvoice about 100 times off contracts ×
   multiplier × Price only warns, naming the quote unit; it never rejects.
4. The `Currency` cell and `Execution Venue` are the only fields that separate codes shared by
   two exchanges (ZS, SI, ZC, CO). With both blank such a row rejects as ambiguous, naming the
   candidates; a populated one that fits no candidate rejects as a contradiction.
5. NetInvoice and Total Fees are in the contract's own currency.
6. A commodity row's Notional is never used to rebuild Quantity (gallons or bushels over a
   multiplier scaled for cents is 100x off); Quantity is rebuilt only from NetInvoice.
7. The `Currency` cell is read as `'DOL.C-USAA'` or a bare ISO code; anything else counts as
   not given.
8. Which rows are the book's is `config/book.yaml` (fund, trader, desk). The sample's Trader
   'JB' and Desk 'JBRV' are made up; Jason's real codes are open question C1.
