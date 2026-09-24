---
name: bloomberg-field-assumptions
description: Unverified Bloomberg field and ticker guesses the ticker check relies on (LME, options on futures, bulk fields), as of 2026-09-24
metadata:
  type: project
---

Nothing below has been seen on a terminal yet (no Bloomberg on the dev PC; the user runs `py 2_launcher.py bbg-check` at the Bloomberg PC). When the user pastes a real report, update this note with what Bloomberg actually returned.

- **Bulk fields (OPT_CHAIN):** `pull_marks.fetch_reference` reads one value per field (`getElement(f).getValue()`), which drops a bulk field's rows, so the check sends its own ReferenceDataRequest (`BlpapiClient.bulk`) and reads the array of sequences row by row. Rows are assumed to carry 'Security Description'.
- **OPT_CHAIN on a generic** ('CL1 Comdty'): assumed to list the options on the current front future. If it comes back empty on the terminal, try the dated future ('CLX6 Comdty') before blaming the root.
- **Option ticker form:** we write 'CLX6C 70 Comdty' (no trailing zeros, one-digit year). Bloomberg's chain may write '70.00'. The check asks our form as well and calls it FORM_MISMATCH only if Bloomberg refuses our form or resolves it to another option.
- **OPT_EXER_TYP** is read as 'American' / 'European' by prefix. OPT_UNDL_TICKER may come without a yellow key.
- **LME:** 'LMCADY' (cash), 'LMCADS03' (3M) and the dated 'LPV6' (monthly) come from engine/lme. The prompt-date field is bbg-curves' `LME_PROMPT_DATE_FIELD` ('FUT_DLV_DT_LAST'). A 3M date one LME business day behind today counts as "not rolled yet", not a mismatch.

**Why:** the user runs the check only when they finally have Bloomberg access (2026-09-24). Every guess here is something the first real run confirms or corrects.
**How to apply:** when a real report arrives, check these guesses first. Record what Bloomberg said, word for word. Related: [[worksheet-fixable-fields]].
