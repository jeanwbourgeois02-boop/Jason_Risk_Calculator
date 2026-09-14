# Excel formula audit

Audited directly from `data/raw/HA-portfolio vJean.xlsx`, reading both formulas and saved values with openpyxl. The workbook was not recalculated or modified. SHA-256: `32f3f6b237780dbcc17902aa28025d1adda00ef0682c6a2f8538e10a884f15be`.

The user's instruction to reproduce this workbook supersedes the former instructions to change its arithmetic. Formula equivalence and numerical agreement with saved Excel results are separate claims; the latter is possible only where Excel saved numeric inputs and outputs.

## FX and futures formula contract

`All FX trades` contains 359 trade rows (2–360). All rows use the following formula pattern, including futures and EURSEK:

| Column | Literal row 2 formula | Meaning |
|---|---|---|
| H | `=$C2*(F2-$E2)/ IF(RIGHT($B2,3)="USD", $E2,F2)` | LTD at yesterday's mark |
| I | `=$C2*(G2-$E2)/ IF(RIGHT($B2,3)="USD", $E2,G2)` | Current LTD |
| K | `=$C2*(J2-$E2)/ IF(RIGHT($B2,3)="USD", $E2,F2)` | LTD at T−2 mark, retaining T−1 denominator |

`C` is the workbook quantity; `E` is fill; `F/G/J` are yesterday/current/T−2 marks. The name test is literally the last three characters of `B`, so `ESU6 Index` and `ESZ6 Index` follow the non-USD-ending branch. There is no extra spot conversion in these formulas. No maturity or trade-date condition exists in the per-row arithmetic.

The workbook quantity is not the BNP base quantity for every instrument:

- USDXXX: workbook C is signed USD base amount.
- XXXUSD, including gold: workbook C is signed USD consideration. With exact legs, recover its sign from the base position and magnitude from the USD leg. For example row 12 AUDUSD: C=2,000,000 and E=0.7006, equivalent to 2,854,695.9748786754 AUD from those exact worksheet inputs. BNP quantities and rounded costs can differ slightly.
- Futures: C is entry notional. Row 8 is `=6*E8*50`; recover C from contracts × multiplier × fill.
- EURSEK: C appears to be base EUR quantity. `Portfolio!B9` multiplies its sum by the EURUSD live rate (`M10`) before position aggregation. The earlier claim that all cross C quantities were USD notionals was not supported. The trade P&L formula still divides by EURSEK's mark and reports the result in the worksheet's P&L column without another conversion. Preserve that literal arithmetic, and do not invent a conversion. The available BNP file predates these cross trades, so cross quantity correspondence cannot be independently reconciled to that file.

## Dates and market data

`M1=TODAY()`, `N1=WORKDAY(M1,-1)`, `O1=WORKDAY(N1,-1)`, `P1=WORKDAY(M1,5)`. No holiday argument is passed. Saved dates are 2026-09-11, 2026-09-10, 2026-09-09, and 2026-09-18 respectively.

`L10=UNIQUE(B:B)`; per-trade F/G/J use XLOOKUP on that spill against N10:N30/O10:O30/P10:P30. The rates panel M cells reference the shared P1, not trade tenor D. Examples:

```text
N11 = BFXFORWARD(L11,M11,"MidOutright","PricingDate",$N$1,"PricingTime","15:00:00-04:00")
O11 = BFXFORWARD(L11,M11,"MidOutright")
P11 = BFXFORWARD(L11,M11,"MidOutright","PricingDate",$O$1,"PricingTime","15:00:00-04:00")
N14 = BDH("ESU6 Index","LAST_PRICE",$N$1)
O14 = BDP(L14,"LAST_PRICE")
P14 = BDH("ESU6 Index","LAST_PRICE",$O$1)
```

Thus yesterday and T−2 FX observations use the **current valuation run's shared P1 target date**, not each observation date's own T+5. Historical pricing time is literally `15:00:00-04:00`; replacing it with winter New York `-05:00` would be a further difference. Saved data only covers September, so winter numerical parity cannot be demonstrated.

One explicit rates-panel exception: **N17=O17 for USDBRL**. Its yesterday rate is today's live outright, not a historical Bloomberg request. This also changes K's F denominator to today's BRL mark. All other FX rows in the panel use the historical request pattern shown above. ESZ6 panel M30 is a fixed 2026-12-18 date; futures pricing itself uses BDH/BDP rather than that forward maturity.

The cash ladder's contractual settlement date and this shared valuation date are different fields and must both be visible. A PB `Price` is a forward mark for its own contract date, not proof of the worksheet's shared-date mark. Spot is useful as a separately displayed general rate, but must not silently replace the worksheet denominator.

## Period and aggregation formulas

The `All FX trades` summary is internally distinct from some `Portfolio` ranges:

| Cell | Exact formula |
|---|---|
| M2 | `=SUM(I:I)` |
| M3 | `=SUMIF(A:A, "<="&N1, H:H)` |
| M4 | `=M2-M3` |
| M5 | `=SUMIF(A:A, "="&M1, I:I)` |
| M6 | `=SUMIF(A:A, "="&N1, H:H)` |
| M7 | `=SUMIF(A:A, "<="&O1, K:K)` |
| M8 | `=SUMIF(A:A, "="&O1, K:K)` |

M9 is blank. There are no 5-day, MTD, or YTD **dollar P&L** formulas in this summary. `pnl time series!M` is labelled YTD %, but is LTD divided by allocated capital, not a year-end dollar difference. Presenting generic period differences as verified workbook formulas would be incorrect.

Normal Portfolio pair rows have C=sum of I, H=sum of H for trade dates `<M1`, I=sum of K for trade dates `<N1`, D=C−H, G=H−I, F=current-day trading P&L and J=prior-day trading P&L. Explicit workbook artifacts that must not be mistaken for equivalent whole-book totals:

- `C2=SUM(C6:C43)`, `D2=SUM(D6:D32)`, `F2=SUM(F7:F26)`, `H2=SUM(H7:H34)`, `I2=SUM(I7:I34)`, `J2=SUM(J7:J26)`, `G2=H2-I2`. Several totals omit row 6 (AUDUSD) and current daily excludes options.
- XAUUSD is row 25; H25 and I25 are blank while D25=C25−H25. This yields full gold LTD as daily P&L under Excel blank arithmetic.
- `G24` (USDZAR prior daily) is the constant `24351.2692045085`, not H24−I24.
- `All FX trades!P3=SUM(ABS(C2:C135))` ignores later trade rows.
- `Portfolio!B3=SUMPRODUCT(((LEFT(A6:A24,3)="USD")*2-1)*B6:B24)+B8`; B4 sums absolute B6:B24. These are not generic currency exposure totals.
- B15/K15 add N42/O42 (USDJPY option deltas) to the sorted USDIDR row. B23/K23 add N37 to USDTWD. B9/K9 include EURUSD and fixed option references. Fixed row formulas cannot be silently realigned while claiming literal workbook equivalence.
- Portfolio market lookup ranges end at rates-panel row 29; ESZ6 exists beyond this range, so its Portfolio rates are `#N/A` even while trade-level F/J are numeric.

A dynamic app aggregation of the FX trade summary can reproduce that summary without reproducing Portfolio's unrelated fixed-range artifacts. It must be described as such, not as exact reproduction of every Portfolio cell.

## Saved numerical reconciliation

All 359 current G and I cells lack numeric results: Bloomberg rates are saved as `#N/A Terminated`/lookup errors and current P&L as `#VALUE!`. All FX historical P&L is also nonnumeric. **There is no saved numeric FX P&L against which to assert full current FX parity.** Do not replace workbook errors with zero or use BNP P&L as an Excel reference.

13 futures rows have numeric F/H/J/K: 8, 37, 58, 97, 114, 216, 226, 268, 296, 301, 358, 359, 360. Direct independent substitution into the literal formulas reproduces all 26 H/K cached outputs with maximum absolute error **0.0 USD** in Python double arithmetic.

| Cell | Inputs | Cached and independently recomputed result |
|---|---|---:|
| H8 | C=2,258,475; E=7,528.25; F=7,598.5 | 20,880.156445351055 |
| K8 | Same C/E/F; J=7,643.75 | 34,329.652233993555 |
| H226 | C=−4,236,162.4799999995; E=7,702.1136; F=7,598.5 | 57,764.564682203934 |
| K226 | Same C/E/F; J=7,643.75 | 32,537.69724520979 |
| H359 | C=−8,052,474.99; E=7,669.0238; F=7,664.5 | 4,752.793575544589 |
| K359 | Same C/E/F; J=7,709.5 | −42,525.094682006464 |

The ten pre-today ESU6 rows sum to H=`257006.9722104244`, K=`207966.90897195626`, difference=`49040.06323846814`, matching `Portfolio!H7/I7/G7`. Rows 358–360 are dated 2026-09-11 and must be excluded from those historical aggregate baselines despite having historical per-row numbers.

An independent database-path review seeded all 13 cached futures trades in an in-memory database with contracts reconstructed as `C/(50*E)` and F/J marks at each contract expiry. `ltd_per_trade` reproduced the 20 date-eligible H/K outputs with maximum absolute difference `1.4551915228366852e-11 USD`; `period_pnl(..., '2026-09-11')` reproduced previous daily P&L `49040.06323846814` exactly. Current LTD correctly remained unavailable because current marks were not supplied. The tiny per-row difference reflects reconstructing quantity through division and multiplication rather than using cached C directly.

Regression strategy: load the workbook read-only, assert the formula strings themselves, feed cached C/E/F/J into the application arithmetic and compare all 26 H/K outputs, then compare these three ESU6 aggregates. For USDXXX/XXXUSD/crosses use explicitly labelled synthetic mark fixtures with the actual Excel formula as independent oracle. Synthetic fixtures demonstrate arithmetic and edge cases, not saved live FX result parity. Include matured trades, weekend target dates, T−2 denominator, current-day cutoff, and missing-mark propagation.

## Other workbook products and limits

The IRS sheet contains 13 numeric H/I/K trade rows. It computes PV from projected cashflows/discount factors; H4 is `Curve!B4*D4*S4*(Q4-G4*P4)`, I4 is `Curve!B4*D4*S4*P4*0.0001`, K4 applies previous FX/annuity/float PV and excludes trades dated today. H2 sums H4:H34, I2 sums I4:I16, K2 sums K4:K27. Saved totals: PV `1878966.5335697`, DV01 `55480.243182718026`, prior PV `1953099.049990575`. `Portfolio!D32=C32-H26` gives `−74132.51642087498`. Merely subtracting entry PV or consuming BNP swap P&L is not this full formula chain.

Options `G=F-E` subtracts **total premium values**, with no Size multiplication. H2/H7/H8/H9 equal G (saved USD results 190784, 418568, 1231910, 89616). H3=G3×Portfolio!M10, H4=G4×M11, H5=G5×M12, H6=G6×M13: these fixed sorted-row references include currencies inconsistent with the option names and currently error. They cannot be silently repaired under exact-parity claims.

`pnl time series` contains manually entered historical LTD and trading values. Its last trading value links to an external workbook (`[1]Portfolio!$F$2`), and last daily P&L is a hardcoded number. This is not a complete recalculable historical mark dataset.

The uploaded HA_PNL file remains the live trade source. The later-dated portfolio workbook is the formula reference, not permission to insert duplicate workbook trades. The saved workbook cannot establish same-input complete-book parity for the earlier BNP snapshot, nor provide working Bloomberg live data. Full current FX numerical reconciliation requires a successfully recalculated Excel save and the identical trade set and mark observations.

## Independent implementation review checkpoints

The initial implementation review identified these checks for integration. These are review checkpoints, not assertions that later revisions remain defective:

- Excel SUMIF with no matching historical trades returns zero. On the first trading day, a priced current book must have daily P&L equal LTD rather than unavailable P&L from a missing historical trade set. Missing rates for existing historical trades must still propagate as unavailable.
- Scalar reconciliation of 26 cached outputs verifies arithmetic only. A database test must additionally exercise contracts-to-C reconstruction, futures mark maturity selection, and the historical date cutoffs before describing the full calculation path as reconciled.
- Crosses must not invent a USD leg. The workbook's EURSEK arithmetic uses C directly and can be reproduced independently of a dollar-entry cash reconciliation; missing USD cash entry and missing workbook arithmetic are different situations.
- Saved rate ingestion must validate historical saved date cells N1/O1 as well as M1/P1 before assigning calendar labels. Formula caches can be stale; assigning expected dates without checking saved dates would overstate certainty.
- The cash ladder now distinguishes contractual cash settlement date, common workbook valuation date, reference spot, original USD entry, workbook valuation and actual PB leg valuation. Its remaining necessary label distinction is that workbook C is not USD notional for every cross.
