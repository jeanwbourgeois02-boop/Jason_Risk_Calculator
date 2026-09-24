"""ui-blotter-fx's own tests (`ui/tabs/blotter_fx.py`). Older tests of this module still
sit in tests/test_ui.py and tests/test_ui_blotter.py."""
from __future__ import annotations

import pandas as pd

from ui.tabs import blotter_fx


def test_fx_scope_keeps_jasons_fx_hedges_and_leaves_futures_and_options_out():
    """Phase 2 (2026-09-24): the sub-tab stays for the FX hedges (USDCNH, EURUSD, USDJPY,
    GBPUSD, EURGBP, XAUUSD); commodity futures and FX options stay on their own sub-tabs."""
    df = pd.DataFrame({
        "trade_id": ["F1", "S1", "C1", "O1"],
        "product": ["FX_FWD", "FX_SPOT", "FUTURE", "FX_OPTION"],
        "instrument_id": ["USDCNH", "EURUSD", "CLZ26 Comdty", "USDJPY111926P-1"],
    })
    assert blotter_fx._scoped_trade_ids(df) == ["F1", "S1"]
    assert blotter_fx.currency_label("USDCNH", "USD", "CNH") == "CNH"
    assert blotter_fx.currency_label("EURGBP", "EUR", "GBP") == "EURGBP"
    assert blotter_fx.currency_label("XAUUSD", "XAU", "USD") == "XAU"


def test_module_names_no_removed_rates_screen():
    """ui/tabs/rates.py leaves in Phase 2: nothing here may point at it."""
    import inspect

    assert "ui.tabs.rates" not in inspect.getsource(blotter_fx)
