"""What counts as a currency hedge in the book (the one test; user decisions 2026-09-28 and
2026-09-29).

A trade is a **hedge** when it is:

- a future on a root of contract-master's ``fx`` sector (the SGX USD/CNH future), or
- an FX spot, forward or swap on a currency pair, or
- an FX option (product ``FX_OPTION``) on a currency pair (user, 2026-09-29: an FX option
  hedges like an FX forward).

A **precious-metal pair** (base or quote XAU, XAG, XPT or XPD: an XAUUSD forward, an XAGUSD
option) is NOT a hedge (user, 2026-09-29): it is a position of its own, an outright under the
Book's rules, whatever its product.

``strategies.py`` (a strategy's hedges, their coverage of its CNY legs, the Daily split),
``period_explain.py`` (the Hedge bucket) and ``trade_type.py`` (which legs the type inference
leaves out as hedges) all read this module, so the Book, the explain and the type agree.
No P&L figure depends on it: it only says which bucket a trade's own P&L is shown in.
"""

from __future__ import annotations

FX_SECTOR = "fx"
FX_HEDGE_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
PRECIOUS_METALS = frozenset({"XAU", "XAG", "XPT", "XPD"})


def pair_currencies(base_ccy: str = "", quote_ccy: str = "", instrument_id: str = "") -> tuple:
    """(base, quote) of an FX trade: the instrument's own currencies, else the first six
    letters of its id ('XAUUSD', 'EURUSD111826C-500041')."""
    base, quote = str(base_ccy or "").strip().upper(), str(quote_ccy or "").strip().upper()
    inst = str(instrument_id or "").strip().upper()
    if not base and len(inst) >= 3:
        base = inst[:3]
    if not quote and len(inst) >= 6:
        quote = inst[3:6]
    return base, quote


def is_precious_pair(base_ccy: str = "", quote_ccy: str = "", instrument_id: str = "") -> bool:
    """True when either side of the pair is a precious metal (XAU, XAG, XPT, XPD)."""
    return bool(set(pair_currencies(base_ccy, quote_ccy, instrument_id)) & PRECIOUS_METALS)


def is_hedge(root, product: str, base_ccy: str = "", quote_ccy: str = "", instrument_id: str = "") -> bool:
    """The book's currency-hedge test (module docstring). ``root``: contract-master's
    ``ContractRoot`` of the instrument's ``base_ccy`` (None for an FX pair); ``base_ccy`` /
    ``quote_ccy`` / ``instrument_id``: the instrument's, to tell a precious-metal pair from a
    currency pair (pass at least one of them for an FX product)."""
    if root is not None and getattr(root, "sector", "") == FX_SECTOR:
        return True
    if str(product) not in FX_HEDGE_PRODUCTS:
        return False
    return not is_precious_pair(base_ccy, quote_ccy, instrument_id)


def fx_non_hedge_why(base_ccy: str = "", quote_ccy: str = "", instrument_id: str = "") -> str:
    """The sentence for an FX-product trade that is not a hedge (a precious-metal pair)."""
    base, quote = pair_currencies(base_ccy, quote_ccy, instrument_id)
    return (f"{base}{quote} is a precious-metal position of its own, not a currency hedge "
            f"(user, 2026-09-29): not paired, an outright inside its strategy")


__all__ = ["FX_HEDGE_PRODUCTS", "FX_SECTOR", "PRECIOUS_METALS", "fx_non_hedge_why", "is_hedge", "is_precious_pair",
           "pair_currencies"]
