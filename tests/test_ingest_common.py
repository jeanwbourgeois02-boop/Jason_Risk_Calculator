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

from datetime import date

import pytest

from data.ingest import common


def test_cash_ccy_mapping():
    assert common.cash_ccy("DOL.C-USAA") == "USD"
    assert common.cash_ccy("TRY.C-TIAA") == "TRY"
    assert common.cash_ccy("XAU.C-XAAA") == "XAU"
    with pytest.raises(ValueError):
        common.cash_ccy("USD")


def test_future_expiry_third_friday():
    root, code, exp = common.future_expiry("ESU6-USAA", date(2026, 8, 17))
    assert (root, code, exp) == ("ES", "ESU6", date(2026, 9, 18))
    assert common.future_expiry("ESZ6-USAA", date(2026, 8, 17))[2] == date(2026, 12, 18)
    assert common.future_expiry("ESH7-USAA", date(2026, 8, 17))[2] == date(2027, 3, 19)
    # year digit rollover: '0' seen from 2029 means 2030
    assert common.future_expiry("ESM0-USAA", date(2029, 8, 17))[2].year == 2030


def test_future_expiry_rejects_unknown_root():
    with pytest.raises(ValueError, match="unknown futures root"):
        common.future_expiry("CLU6-USAA", date(2026, 8, 17))  # crude: not an equity-index root
    with pytest.raises(ValueError, match="unrecognised futures symbol"):
        common.future_expiry("ES-USAA", date(2026, 8, 17))


def test_description_regex_accepts_both_orders_and_rejects_variants():
    ok1 = "TD 08/03/2026 VD 09/16/2026 SELL USD VS .BUY TRY @ 49.21249500"
    ok2 = "TD 08/03/2026 VD 09/16/2026 BUY USD VS .SELL TRY @ 49.21249500"
    assert common.DESCRIPTION_RE.match(ok1)
    assert common.DESCRIPTION_RE.match(ok2)
    assert not common.DESCRIPTION_RE.match(ok1.replace(" .BUY", " BUY"))  # dot is mandatory
    assert not common.DESCRIPTION_RE.match(ok1[:-1])  # 7 decimals


def test_irs_symbol_and_description_regexes():
    m = common.IRS_SYMBOL_RE.match("IRSOIS-USD-22860996")
    assert m.groups() == ("USD", "22860996")
    assert not common.IRS_SYMBOL_RE.match("IRSFF-USD-1")  # only the OIS prefix is in scope

    dm = common.IRS_DESCRIPTION_RE.match("IRS NA 11/11/2026 02/11/2027 3.98000000 USD")
    assert dm.groups() == ("NA", "11/11/2026", "02/11/2027", "3.98000000", "USD")


def test_forward_symbol_regex():
    m = common.FORWARD_SYMBOL_RE.match("USDTRY091626-196789440")
    assert m.groups() == ("USDTRY", "091626", "196789440")
