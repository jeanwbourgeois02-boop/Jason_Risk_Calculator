Now implement the FX cash/exposure ladder inside the existing VS Code risk-monitor project.

Do not create a separate application. Preserve the existing project structure and make only the changes required for this feature.

Apply the Bloomberg architecture established in the previous step. The cash-ladder calculation must work on the development computer using the mock or recorded market-data provider. Do not block the ladder on Bloomberg being available.

This phase is narrowly limited to:

* FX spot;
* FX forwards;
* FX swaps where the two legs can be identified;
* settlement-date currency exposure;
* live USD translation;
* screenshot-compatible reference P&L;
* trade-level drill-down;
* validation and tests.

Do not build futures, options, interest-rate swaps, full portfolio analytics, VaR or a general redesign yet.

## Core objective

Upload a prime-broker FX trade export and produce a settlement-date currency exposure ladder with live USD translation and transparent P&L.

Use these supplied reference files:

* the cash-ladder screenshot;
* `HA-portfolio vJean.xlsx`;
* `HA_PNL_20260818.csv`.

The screenshot and workbook are behavioural references. Do not assume every existing workbook formula is correct. The workbook contains Bloomberg Excel formulas and may show `#VALUE!` or `#N/A` when Bloomberg is unavailable.

## Ladder layout

The main view must resemble the screenshot:

```text
Settlement Date | Book | AUD | BRL | CAD | CHF | JPY | SEK | ...
```

Use:

* settlement dates as rows;
* book as a grouping/filter field;
* dynamic currency columns;
* signed local-currency amounts in each date/currency cell.

The rows must use exact value or settlement dates, not trade dates.

The user must be able to filter by:

* book;
* account or fund;
* strategy;
* settlement-date range;
* as-of date;
* currency;
* product type.

The default book filter should reproduce the screenshot’s `HA` view if the uploaded data contains an equivalent book mapping.

## Interpretation of the screenshot

Treat the screenshot as a broker-style exposure and P&L ladder.

Do not automatically interpret it as a conventional bank-account cash statement.

For this first implementation, use the broker-reference sign convention. Preserve a configurable field called `sign_convention` with these possible values:

```text
broker_reference
economic_currency
physical_settlement_cash
```

Use `broker_reference` by default.

Do not silently switch between these modes.

The broker-reference mode must preserve the signed direction supplied by the broker’s reporting convention. Do not reverse signs merely because a physical cash-flow interpretation feels more intuitive.

The economic-currency and physical-settlement views may be prepared for later extension, but they are not the primary calculation in this phase.

## Summary block

Below the ladder, show a summary block for every currency:

```text
FX rate
Local delta
USD delta
USD delta entry
P&L
```

Calculate:

```text
Local delta[currency]
    = sum of all signed local amounts for that currency

USD delta[currency]
    = Local delta[currency] × live USD-per-local-currency rate

USD delta entry[currency]
    = sum of the trade-level entry USD amounts associated with that currency

P&L[currency]
    = USD delta[currency] − USD delta entry[currency]
```

Do not calculate the entry value using one assumed average rate.

Every trade must retain its own agreed rate. Calculate its entry USD amount at trade level, then aggregate those amounts.

If an implied entry rate is displayed, calculate it only as:

```text
USD delta entry / Local delta
```

Label it `Implied entry rate`. It is informational and must not replace the individual trade rates.

The AUD screenshot fixture is:

```text
9/11/2026:  AUD -20,763,351
9/16/2026:  AUD +9,764,499
9/24/2026:  AUD -21,109,276
9/28/2026:  AUD +11,146,505

Local delta:     -20,961,623 AUD
FX rate:          0.600 USD/AUD
USD delta:       -12,576,974 USD
USD delta entry: -15,000,000 USD
P&L:              +2,423,026 USD
```

The calculation must reproduce:

```text
-20,961,623 × 0.600 = -12,576,974

-12,576,974 − (-15,000,000) = +2,423,026
```

Use a documented rounding tolerance. Do not hide a material difference.

## Input data and normalization

Accept CSV and XLSX prime-broker exports.

The supplied CSV contains fields including:

```text
Account
Base Currency
Bloomberg Identifier
Business Unit
Cost
Currency
Financial Type
Fx
Local Cost
Market Value Base
Market Value Local
Price
Position
Quantity
Symbol
Symbol Description
Trade Factor
DTD Total P&L
DTD Trading P&L
YTD Total P&L
YTD Trading P&L
```

Create a canonical normalized FX record containing at least:

```text
trade_id
source_row_id
product_type
symbol
symbol_description
trade_date
settlement_date
currency_pair
base_currency
quote_currency
local_currency
local_amount
usd_entry_amount
entry_rate
direction
book
account
fund
strategy
source_quantity
source_local_cost
source_cost
source_price
source_fx
raw_source_row
validation_status
validation_message
```

Preserve the complete raw source row.

Do not assume that `Quantity` always represents USD.

The supplied export contains descriptions such as:

```text
SELL USD VS .BUY AUD @ 0.70060700
SELL AUD VS .BUY USD @ 0.69469100
SELL USD VS .BUY JPY @ 162.27029909
SELL CHF VS .BUY USD @ 0.80709000
```

Parse:

* trade date from `TD`;
* settlement date from `VD`;
* pair currencies;
* buy/sell direction;
* agreed rate following `@`.

Support variations in spacing, punctuation and capitalisation.

The application must derive the local amount and entry USD amount using the pair, direction and broker field mapping.

Do not assume that `Quantity` always represents the same currency.

Do not assume that `Local Cost` is always the opposite currency.

Use `Quantity`, `Local Cost`, `Cost`, `Position`, `Currency`, the pair and the entry rate as validation evidence.

For every trade, show:

```text
source amount
derived amount
difference
validation status
```

If the difference exceeds a configurable tolerance, flag the trade instead of silently choosing a number.

If a trade cannot be mapped confidently, put it in an `Unresolved trades` table and exclude it from the clean ladder until corrected.

## Multiple trades at different rates

Several trades may involve the same pair, currency and settlement date.

The ladder must:

* retain every trade separately internally;
* sum local amounts in the displayed date/currency cell;
* calculate USD entry value from each trade’s own rate;
* provide a drill-down showing every contributing trade.

Example:

```text
Trade 1: AUD +100,000 at 0.6500
Trade 2: AUD +200,000 at 0.6700
```

Expected:

```text
Local delta = +300,000 AUD

USD delta entry
    = 100,000 × 0.6500
    + 200,000 × 0.6700
    = 199,000 USD
```

Do not use FIFO or a weighted-average rate internally.

Do not implement realised/unrealised lot matching in this phase.

## FX conversion

Use the market-data interface from the Bloomberg architecture prompt:

```text
get_spot_rate(currency_pair, as_of)
get_historical_rate(currency_pair, date)
```

Normalize every conversion into:

```text
USD per unit of local currency
```

Examples:

```text
AUDUSD = 0.7000
USDJPY = 150.00
```

Therefore:

```text
USD per AUD = 0.7000
USD per JPY = 1 / 150.00
```

Do not infer quote direction from the number of decimal places.

Store an explicit `inverted` flag.

For the first working version:

* use the mock or recorded provider on the development computer;
* show the rate source and timestamp;
* mark missing or stale rates visibly;
* never substitute zero for a missing rate;
* never fabricate Bloomberg data.

## Settlement dates and swaps

Use the value date or settlement date from the trade, not the upload date.

Distinguish:

* past or settled dates;
* today’s settlement;
* future settlement dates.

The default macro-risk view should show current and future exposures.

Do not treat a future settlement as a current bank balance.

For FX swaps, represent the near and far legs independently:

```text
near leg:
    near settlement date
    currency amounts

far leg:
    far settlement date
    reverse currency amounts
```

If the prime-broker export does not identify two rows as the legs of one swap, do not invent the relationship. Treat them as separate FX trades and flag the missing swap linkage.

## P&L scope

Implement the screenshot-style reference exposure P&L:

```text
current USD delta − USD delta entry
```

Also calculate trade-level reference P&L where the necessary fields exist.

Keep this distinct from full discounted forward mark-to-market.

Do not implement interest-rate curves, carry attribution or full derivative MTM in this phase.

## User interface

Implement only the screens required for this feature:

1. File upload.
2. Field mapping and validation.
3. Exposure ladder.
4. Trade drill-down.
5. Market-data status.

The ladder must show:

* as-of date;
* selected book;
* selected sign convention;
* last refresh time;
* rate source;
* stale-rate indicator;
* unresolved-trade count.

Clicking or expanding a date/currency cell must show:

```text
trade ID
symbol
description
trade date
settlement date
currency
local amount
entry USD amount
entry rate
book
source row
validation status
```

## Tests

Create automated tests for:

1. Exact AUD screenshot reproduction.
2. Multiple trades on the same date at different entry rates.
3. Multiple settlement dates.
4. AUDUSD quote direction.
5. USDJPY inverse USD-per-local conversion.
6. Missing settlement date.
7. Malformed `Symbol Description`.
8. Missing FX rate.
9. Duplicate upload does not double-count trades.
10. Cell drill-down includes every contributing trade.
11. Book filtering.
12. As-of-date filtering.
13. Broker-reference sign convention remains distinct from economic-currency sign convention.

Do not move on to another asset class until these tests pass.

At the end, report:

* files changed;
* tests run;
* exact AUD fixture output;
* unresolved source-field ambiguities;
* whether Bloomberg was actually connected;
* which values were excluded from the ladder and why.

Do not claim the cash ladder is complete merely because the interface renders. The normalization, formulas, reconciliation and drill-down must all be tested.
