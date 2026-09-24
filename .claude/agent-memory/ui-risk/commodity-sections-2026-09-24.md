---
name: commodity-sections-2026-09-24
description: How the Risk tab renders the commodity book (Phases 4/5): parts vs views tables, sector grouping, commodity cards, commodity stress, margin estimate and limit checks; the display rules chosen and why
metadata:
  type: project
---

Built 2026-09-24 from the housekeeper's brief ("ui-risk renders both" + margin-limits).
Decisions taken (keep unless the user says otherwise):

- **Parts vs views.** Main table = rows the Book sums (FX, METAL, COMMODITY); SECTOR and
  SPREAD rows (`role == 'view'`) go in a separate `risk-views-table` under the heading
  "Views (not added to the Book)", label columns shaded/italic. Nobody may sum them.
- **Grouping.** FX / metal rows in engine order first, then COMMODITY rows grouped by sector
  (sector order = where its largest commodity comes). A "Sector" column; commodity shows as
  "WTI crude (NYMEX:CL)"; contracts / legs / parts + reason on the name's tooltip.
- **Book footer Net/Gross USD are FX + metal only** (engine's `book.net_usd`); the commodity
  net/gross are separate cards and the footer cell's hover says so. Summing them is the
  engine's call, not the screen's.
- **vol_note** ("crisis window not in history: trailing vol only") is the hover of the
  blended and crisis vol cells and is appended to the Blended vol card note.
- **Net USD card reason** uses only FX/metal `missing` entries (`delta_missing`), so the
  commodity rows' missing entries never explain an FX n/a.
- **Commodity scenarios:** sector columns ids `sector_<i>` (names as headers); blank = not
  moved; total n/a with reason; partial total gets "excludes N" note + missing on hover;
  one collapsed `html.Details` per scenario (by root / contract / spread / currency).
- **Margin:** heading "Margin (estimate, not exchange SPAN)". margin-limits' roll-ups sum
  only positions with a figure, so an all-excluded sector is 0.0 in the engine: the screen
  shows n/a with the reason instead (a sum over nothing is not a zero margin).
- **Limits:** level colours keyed on the DISPLAYED level text (filter_query on a data key
  that is not a column was avoided); NOT_SET reads "not set in config/limits.yaml", grey.
- `render` calls `margin_and_limits` (curve_positions + book_spreads once, passed to both);
  `book_risk` still computes its own curve/spreads internally (asked risk-metrics for
  `curve=` / `spreads=` kwargs).

**Why:** hard rule "no figure blank without its reason, never zero for missing", and the
views re-add parts (double counting if summed).
Related: [[risk-tab-layout-decisions]], [[retired-macro-underlyers]].
