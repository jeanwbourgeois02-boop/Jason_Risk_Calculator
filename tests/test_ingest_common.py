"""Tests for data/ingest/common.py: the shared dataclasses/regexes/helpers the live
blotter parser (data/ingest/blotter.py) depends on.

Extracted 2026-09-17 from tests/test_ingest.py when data/ingest/bnp.py (the retired BNP
CSV parser these symbols used to live in) was deleted along with everything else BNP-
specific ("no bnp fall back", docs/bnp-excel-removal.md). Only the symbols that moved to
common.py are re-tested here; BNP-format-specific helpers that did NOT move
(`_previous_weekday`, `file_date_from_name`, `ReconReport`) had no live successor and
their tests were removed, not moved.
"""
from __future__ import annotations

import pytest

from data.ingest import common


def test_cash_ccy_mapping():
    assert common.cash_ccy("DOL.C-USAA") == "USD"
    assert common.cash_ccy("TRY.C-TIAA") == "TRY"
    assert common.cash_ccy("XAU.C-XAAA") == "XAU"
    with pytest.raises(ValueError):
        common.cash_ccy("USD")


def test_description_regex_accepts_both_orders_and_rejects_variants():
    ok1 = "TD 08/03/2026 VD 09/16/2026 SELL USD VS .BUY TRY @ 49.21249500"
    ok2 = "TD 08/03/2026 VD 09/16/2026 BUY USD VS .SELL TRY @ 49.21249500"
    assert common.DESCRIPTION_RE.match(ok1)
    assert common.DESCRIPTION_RE.match(ok2)
    assert not common.DESCRIPTION_RE.match(ok1.replace(" .BUY", " BUY"))  # dot is mandatory
    assert not common.DESCRIPTION_RE.match(ok1[:-1])  # 7 decimals


def test_forward_symbol_regex():
    m = common.FORWARD_SYMBOL_RE.match("USDTRY091626-196789440")
    assert m.groups() == ("USDTRY", "091626", "196789440")


def test_the_products_that_left_the_app_leave_no_names_behind():
    """Phase 2 of the commodity conversion (user, 2026-09-24): the IRS regexes and direction
    record, the equity index futures' roots / multipliers / expiry rule and the NDF lists and
    tickers are gone."""
    for name in ("IRS_SYMBOL_RE", "IRS_DESCRIPTION_RE", "IrsDirection", "NO_DIRECTION_SIGNAL",
                 "NDF_CCYS", "NDF_1M_TICKERS", "NDF_FIX_TICKERS",
                 "FUTURE_MULTIPLIERS", "KNOWN_FUTURE_ROOTS", "FUTURE_SYMBOL_RE",
                 "FUTURE_MONTH_CODES", "future_expiry", "third_friday"):
        assert not hasattr(common, name), name
