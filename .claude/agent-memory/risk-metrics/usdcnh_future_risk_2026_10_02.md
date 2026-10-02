---
name: usdcnh-future-risk-2026-10-02
description: How the SGX USD/CNH future gets its risk series (own closes, else the USDCNH close by its fx-sector root), the stale 'UCV26 Curncy' ids behind the 2026-10-02 bug, and why synthetic history makes the hedge look harmful.
metadata:
  type: project
---

Bug fixed 2026-10-02 (user "do that"): the Risk drawer said "UCV26 Curncy is not a SGX:XUC contract" and the
SGX USD/CNH hedges of ZNA1 / SILARB1 were left out of daily risk, hedge %, VaR.

Cause: contract-master moved the root's Bloomberg root from UC to XUC on 2026-09-30 (UC is B3's USD/BRL). A
database uploaded before that keeps instruments 'UCV26 Curncy' (bbg_ticker 'UCV6 Curncy') under base_ccy
'SGX:XUC': `contract_for` refuses the id (curve row has lots and delta but no month), and risk-history's
`_our_root_of` cannot place 'UC..' closes, so the contract had no series. A re-upload re-keys the trades to
'XUCV26 Curncy' (merge by Trade Id).

Fix (commodity.py `_PerLot.get` + `fx_future_pair`; metrics.py `_commodity_positions`): an fx-sector root
(`hedges.is_hedge`) with no own series moves on its pair's close, size_unit + currency = 'USDCNH', change x
100,000 x lots x USD per CNH that day (same formula as own closes). metrics drops a curve reason whose row still
carries a delta from `missing`. Headline VaR still equals the positions' VaR.

**Why:** the hedge must be counted whatever its stored id; root decides, never a ticker string.
**How to apply:** on the ui lane's synthetic hist.db SHFE prices do not move with USDCNH, so counting the hedge
LOWERS hedge % (ZNA1 -70 -> -75). With import parity scaled in (scratch parity.py) ZNA1 goes -66 -> -57 and daily
risk falls: judge hedge direction only on real Bloomberg history. FX unhedged is spreads-engine's notional
figure (trade_book hedge block), never history-based. See [[fx-trades-in-trade-risk-2026-09-30]].
