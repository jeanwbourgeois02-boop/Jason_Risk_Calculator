---
name: research-db-quirks
description: HISTORY ONLY since 2026-09-30 (rv.sqlite no longer read) - quirks of the research db the old commodity_history.py depended on
metadata:
  type: project
---

Facts about `../Commodity Dashboard/var/rv.sqlite`, checked 2026-09-24 on the dev PC (mock data there; the real data is on the Bloomberg PC):

- **Contract ids differ for about half the roots.** 102 of 202 roots have the research app's placeholder Bloomberg root (`ZZWR`) where our `config/contracts.csv` has the Phase 2 guess (`WR`), so `WRF27 Comdty` (ours) is `ZZWRF27 Comdty` there. price_scale and currency matched on all 202 roots. Match on (root_id, year, month) when the id is not found as it is.
- **Units.** `settle` is Bloomberg's raw quoted price; our `multiplier` is per 1.0 of that raw price. P&L = Δraw × multiplier; Δ(raw × price_scale) × multiplier is price_scale times too small on the 17 roots with scale 0.01 (RB, HO, ZC, ...). The housekeeper's brief assumed quote units; I corrected that.
- **WAL side files.** A `mode=ro` open of this WAL database leaves an empty `-wal` and a `-shm` beside it (a read-only connection cannot clean up). The database is untouched. An empty WAL is left out of the cache key so the first read does not invalidate its own entry.
- **Depth.** `calendar_depth` varies (SHFE:WR 1, LME:NI 5, NYMEX:CL 24), so a deferred contract often has no history of its own and no constant-maturity series at its rank. The reason says so.
- **Range.** Dev copy: 2020-09-21 to 2026-09-22. So the negative-WTI replay (2020-04-17..20) is not covered here. Whether the Bloomberg PC's copy goes back further is unknown.
- **Curve units.** `research_curve` gives `settle` in quote units (raw x price_scale) and `raw_settle` as stored; our FUTURE_PX marks are Bloomberg's raw quoted price, so a screen comparing with our marks must use `raw_settle` (or scale ours). Dev mock expiries are not real (CLU26 on 2026-09-30).
- The FX pairs stored are all USD-base (USDCNH, USDCNY, USDEUR, USDGBP, USDJPY, USDMYR, USDCAD). CNY goes through USDCNH by default (the research app's rule).

- **Open interest and volume** (checked 2026-09-29 for `contract_liquidity`): `price_daily.open_interest` / `volume` are Bloomberg OPEN_INT / PX_VOLUME per contract, rebuilt from generics 1..calendar_depth, so a deferred month beyond the depth has no figures at all. The research app pulls volume only for its last 400 days (`BBG_VOLUME_HISTORY_DAYS`): older real rows can have NULL volume. Its mock notes the Chinese exchanges' January 2020 switch from double- to single-sided OI counting and does not model it; mock vol/OI is ~0.7-0.8 on SHFE/INE/DCE vs ~0.06 on COMEX/NYMEX/LME (generator turnover, not evidence). LME is the research app's monthly contract (LPZ26 for copper Dec), not a prompt. Mock DB rebuilt 2026-09-28, data to 2026-09-28.

**Why:** these facts decide whether a position's risk history is right or silently empty.
**How to apply:** re-check the root and depth differences on the Bloomberg PC's copy, and after any contract-master change to `bbg_root`. See [[dev-pc-parquet-and-package-init]].

- **Provenance** (2026-09-29): rows carry no provider; the research app's `job` table does (`provider` 'mock' | 'bloomberg' | '', kinds pull / backfill write prices; `pull_log` maps job to ticker). The dev copy has one job, `pull` by `mock` on 2026-09-28, so every figure drawn from it is generated: Jason's fills sit 30-60 % away (COMEX:SI x0.38, COMEX:HG x0.47, CME:HRC x0.49). Another agent once described these figures as real. `research_source()` / `status()['source_kind']` and `research_price_check(conn, as_of)` now say so; any mock job makes the kind 'mock' (its rows can survive a later Bloomberg pull).
