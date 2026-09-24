"""The commodity curve positions (curve-positions lane): what the book is long or short, in
which contract month, in lots, in physical units, in USD notional and in delta (futures,
options on them, LME forwards; the declining delta of monthly-average contracts).

``curve_positions(conn, as_of)`` is the one entry point; ``positions.py`` holds it, ``rows.py``
builds one row per position, ``averaging.py`` the averaging contracts' delta factor.
"""

from engine.curve.averaging import averaging_factor
from engine.curve.positions import curve_positions
from engine.curve.rows import month_key

__all__ = ["averaging_factor", "curve_positions", "month_key"]
