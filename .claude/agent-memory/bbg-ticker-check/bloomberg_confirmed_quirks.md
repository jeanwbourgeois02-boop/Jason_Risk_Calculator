---
name: bloomberg-confirmed-quirks
description: Bloomberg behaviour confirmed on the terminal (2026-10-01): COMEX copper CRNCY 'USD' though quoted in cents; CME HRC on EXCH_CODE 'CMX'
metadata:
  type: project
---

Confirmed from the Bloomberg PC's diagnosis report, 2026-10-01:

- COMEX copper (HG1 Comdty): CRNCY is 'USD', not 'USd', although PX_LAST is in cents/lb (657.15) and FUT_VAL_PT is 250 (= our multiplier at price_scale 0.01). So CRNCY's minor-unit casing is NOT a reliable witness of cents; FUT_VAL_PT is.
  **Why:** the check raised a false SCALE_MISMATCH on it.
  **How to apply:** in the scale check, a value per point that agrees with the multiplier is decisive; the currency is the only witness only when no value per point came back.
- CME US Midwest HRC (root CME:HRC): Bloomberg lists HRCV6/X6/Z6/F7 on EXCH_CODE 'CMX' (CME Group lists HRC on its COMEX venue). Handled by a per-root override (ROOT_EXCHANGE_CODES), not by making CMX valid for every CME root.

Related: [[bloomberg-field-assumptions]] (the still-unverified guesses).
