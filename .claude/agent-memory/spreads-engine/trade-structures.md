---
name: trade-structures
description: 2026-10-02 structures inside a trade found from the fills (engine/spreads/structures.py) - finder order, the choices the spec left open, 7.1 avg per (structure, contract), quote/hedge-link rules, what the real book became, how to verify
metadata:
  type: project
---
User yes 2026-10-02 (incl. hard rule 7 for the open/locked split). `structures.find_structures` replaced
`_decompose` + `_split_parts` as the entry point of `trades._trade` (those two still run, on the pooled fills).

Choices I took (re-review by Fable pending):
- Rule 1 (one structure, no tickets) only for a VALUE-sized one_spread (two subsectors, CATTLE). **Why:**
  strategies' one_spread also covers one commodity on two exchanges (ZNA1, COPAR3); skipping tickets there hid the box.
- A same-day calendar ticket is a roll (not a calendar) only if `rolls.py` names it AND the root was not flat
  before that day. **Why:** STEEL's Nov/Dec 200 is a "roll" to rolls.py but STEEL held a calendar (net 0).
- Tickets merge by (root, contract pair) whatever day or direction; boxes only from two TICKET calendars.
- Loose pooled contract: flat one -> the part whose closed_roots has its root, else the one structure holding it,
  else the one holding its root; open one -> the one structure holding it; else UNMATCHED (of_kind "", so the
  trade type ignores it: COPAR3 stays CROSS_EXCHANGE). PIN structures are exact (never take loose fills).
- Hedges: fill-against-fill hedge calendars first (ZNA1's XUC V/X 125 sat in a day with other XUCX6 sells), then
  per contract by China-leg month, the not-yet-hedged one when two qualify.
- Sub `legs` stay OPEN legs only (risk reads their contract ids); new `closed_legs`, `hedge_legs`. Closed
  structures sorted last with `legs` [] (ui filters subs by legs; risk's `spread` index stays aligned).
- Quote blank for a 2-leg value-sized pair (different subsectors, no crack): a 1:1 difference means nothing.
- Trade-level leg avg_fill switched to 7.1 too; its open = sum of its structures' opens when all its fills are in
  structures (so it is NOT lots x (mark - avg_fill) when two structures hold it, e.g. STEEL HRCX6).
- Iron ore unit stays contracts.csv's USD/t (not the spec's "dmt"). The "$/st" the user saw is NOT in
  engine/spreads output (HEAD and now both give USD/t on SCO1's iron ore subs and render $/t on a db without
  price_history): look in risk z hovers / ui with price history on the Bloomberg PC.

Real book on the spec fixture (scratch/spreads: setup.py, snap.py, cmp.py, show.py, cover.py): every 10.2/10.3
figure matched to the cent (XUCZ6 locked 7,770.005 -> spec 7,770.01). LTD per trade/leg/book unchanged on the
real db, a made-up-marks copy and the golden sample (vs a `git archive HEAD` copy); only STEEL (phantom -69,000
gone), SILARB1 XUCZ6 (7.1 vs day-avg) and float dust moved in the split. Supersedes the STEEL "Oct/Dec 175 +
Nov/Dec 25" and ZNA1 "month pairs" of [[equal-size-spreads]]; avg rule of [[locked-open-split]] is now 7.1.
