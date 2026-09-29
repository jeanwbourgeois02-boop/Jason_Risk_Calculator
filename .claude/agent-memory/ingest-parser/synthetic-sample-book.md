---
name: synthetic-sample-book
description: data/sample/blotter_sample.csv became Jason's synthetic commodity + FX-hedge book on 2026-09-24 (macro blotter out); its composition, ids, and the option-Description letter hazard found building it
metadata:
  type: project
---

On 2026-09-24 (Phase 2 wave 0) the user had the macro trader's REAL blotter removed from
`data/sample/blotter_sample.csv`; it is now a synthetic book, and
`commodity_blotter_sample.csv` was deleted (its rows were merged in).

**Why:** real trader / fund / counterparty data must not sit in the tree; the golden book and
about a dozen lanes' tests load that path, so the replacement kept the path.

**How to apply:**
- Composition (45 rows until Phase 5; 52 rows since, see [[phase5-options-and-lme]]):
  31 FUTURE rows (29 trades + 2 UNRECOGNISED since 2026-09-29: bare ZCZ6 ambiguous, QQZ6-USAA unknown), 8 FORWARD
  (USDCNH x3 incl. 910000034 settled 2026-08-19; EURUSD, USDJPY, GBPUSD, EURGBP cross,
  XAUUSD), 1 CURRENCY spot (910000040 EURUSD), 5 OPTION (EURUSD call 500041, USDJPY put
  500042, USDJPY "digital" 500043 with no strike and no payoff word, closed-out EURUSD put
  pair 500044 buy / 500045 sell). Trade Ids 910000001-045, Trader JB, Desk JBRV, Fund NMMF,
  counterparties CPTY-A (futures) / CPTY-B (FX) / CPTY-C (options), accounts PB-*-NMMF.
- 2026-09-28: rows 9-17, 24-25, 33 book their cents-quoted roots in the broker's units (455.2 -> 4.552;
  NetInvoice unchanged, since invoice = lots x contract size x broker price), BZ6-UKAA, FEFF7-SPAA,
  and the ambiguous row is bare ZCZ6; the parse (50 trades, ids, fills) equals the pinned golden book.
- 2026-09-29 (Phase G, spreads-engine's request): every row carries a PBRoot, the ONLY change
  (edited by a csv round-trip script that asserts each line re-serialises byte-identical first).
  WTICAL1 .5 (001-004), BRWTI1 .4 (005-006: the deliberate type mismatch, the rule says cross
  exchange since Brent and WTI share subsector crude_oil), CRACK1 .4 (007-009), CRUSH1 .4
  (010-012), CORN1 (013, 032 reject), GOLD1 (014, 048 option), SILVER1 (015), COPAR1 .3 (016-018 +
  USDCNH fwds 031 and settled 034, long USD/CNH against the short SHFE leg), IRON1 .3 (019-020 +
  USDCNH 030 1.5m: the deliberate hedge_oversized, 14x), TTFNBP1 .4 (021-024 + 035, 037, 038, 040;
  TFM and M are both exchange ICE, so the rule says cross product, not .3), GOLDJP1 .3 (025-026 +
  USDJPY 036), WTI1 (027-029), MISC1 (033 reject), XAU1 (039), EURVOL1 (041, 044, 045), JPYVOL1
  (042-043), WTIRR1 (046-047), CUVOL1 (049), CUAL1 .4 (050-051), NICKEL1 (052). 0 unassigned.
  The golden book pins `"strategy":""` on its value_book rows, so this needed a re-pin (user's yes).
- Regenerate from a script, never by hand: NetInvoice must stay consistent (tests assert
  no warnings). The generator lived in the session scratchpad; its logic is simple enough
  to rewrite (forward: quote = round(base x rate, 2); option: NetInvoice = notional x premium).
- HAZARD: `_option_terms_from_text` reads any standalone word C / P in an option
  Description as CALL / PUT (CALL checked first). A counterparty code like 'CPTY-C' at the
  end of a Description would turn every put into a contradiction reject. The sample's
  option Descriptions therefore end at the expiry. Not fixed (wave 0 froze blotter.py);
  worth fixing if a real export puts such a token there.
- `data/raw/new_sample_trades.csv` (untracked, real) is still read by other lanes' tests
  (ladder, library, live, options, rates, ui_*); they skip when it is absent.
- Related: [[commodity-futures-ingest]].
