"""Portfolio/position layer: turning individual pricer outputs into
position-level and book-level risk.

WHERE THIS SITS IN THE CHAIN
-----------------------------
Every pricer in equity/ and fx/ (and every structure in structures.py)
returns Greeks PER SINGLE UNIT of the underlying -- "this option's delta
is 0.48" says nothing about how much of it you actually hold. This module
adds the next layer up: scale a pricer's output by how many units you
hold (the "quantity"), then sum many such positions -- possibly on
different underlyings, possibly different asset classes -- into one net
risk picture. No pricing math happens here; this is pure aggregation on
top of dicts this package already produces.

USAGE
-----
    from options_calc import price_european_equity_option, price_european_fx_option
    from options_calc.portfolio import Position, Portfolio

    positions = [
        Position(
            label="SPX 5600 Call",
            asset_class="equity",
            result=price_european_equity_option(S=5500, K=5600, T=0.25, r=0.045, sigma=0.15, option_type="call"),
            quantity=100,   # long 100 units, already USD -- reporting currency, no conversion needed
        ),
        Position(
            label="EURUSD 1.10 Put",
            asset_class="fx",
            result=price_european_fx_option(S=1.09, K=1.10, T=0.25, domestic_rate=0.045,
                                             foreign_rate=0.0325, sigma=0.08, option_type="put"),
            quantity=-50000,   # short 50,000 units
            # This FX option's price/Greeks come out in USD per the pair's
            # own convention already (domestic = USD here), so fx_rate_to_base
            # stays at its default of 1.0. If a position's own result were
            # instead denominated in a THIRD currency relative to the book's
            # reporting currency, fx_rate_to_base is where you'd supply that
            # conversion rate -- see Position's docstring for why this
            # matters and is not automatic.
        ),
    ]
    book = Portfolio(positions)

    book.total()             # {price, delta, gamma, theta, vega, rho} summed across everything
    book.by_asset_class()    # {"equity": {...}, "fx": {...}} -- one subtotal per asset class
"""

from .structures import combine


class Position:
    """One instrument held in some quantity.

    label: a human-readable name for this position (e.g. "SPX 5600 Call")
    asset_class: a grouping key, e.g. "equity" or "fx" -- used by
        Portfolio.by_asset_class() and by_label() to produce subtotals;
        this package's convention is "equity" or "fx", but any string works
    result: the dict returned by any pricer or structure in this package
        ({price, delta, gamma, theta, vega, rho, ...})
    quantity: signed number of units held -- positive for long, negative
        for short (matches the (result, quantity) convention combine()
        already uses)
    fx_rate_to_base: the conversion rate from this position's OWN quoting
        currency into the portfolio's reporting currency, e.g. if this
        position is quoted in EUR and the book reports in USD, this is the
        EURUSD rate. Defaults to 1.0 -- correct only when every position in
        a Portfolio already shares one common quoting currency; REQUIRED to
        be set correctly whenever positions span multiple currencies (e.g.
        an equity position priced in its local currency alongside an FX
        position), otherwise Portfolio.total() silently adds raw numbers
        that are not actually comparable. This package does not fetch or
        know FX rates itself -- the caller supplies this.
    """

    def __init__(self, label, asset_class, result, quantity, fx_rate_to_base=1.0):
        self.label = label
        self.asset_class = asset_class
        self.result = result
        self.quantity = quantity
        self.fx_rate_to_base = fx_rate_to_base

    def scaled(self):
        """This position's contribution to portfolio Greeks, in the
        portfolio's reporting currency: the pricer's per-unit output
        scaled by quantity, then converted via fx_rate_to_base."""
        return combine((self.result, self.quantity * self.fx_rate_to_base))


class Portfolio:
    """A collection of Positions, with book-level aggregation."""

    def __init__(self, positions):
        self.positions = list(positions)

    def total(self):
        """Net {price, delta, gamma, theta, vega, rho} across every
        position in the book, each converted to the reporting currency via
        its own fx_rate_to_base -- the "Portfolio Totals" row in a risk
        grid."""
        legs = [(p.result, p.quantity * p.fx_rate_to_base) for p in self.positions]
        if not legs:
            return {"price": 0.0, "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
        return combine(*legs)

    def by_asset_class(self):
        """Subtotals grouped by each position's asset_class -- e.g. the
        separate "Equity" and "FX" subtotal rows in a risk grid."""
        return self._group_by(lambda p: p.asset_class)

    def by_label(self):
        """Subtotals grouped by each position's label -- useful when
        several Position entries share one label (e.g. multiple lots of
        the same instrument) and should be combined into one row."""
        return self._group_by(lambda p: p.label)

    def _group_by(self, key_fn):
        groups = {}
        for position in self.positions:
            groups.setdefault(key_fn(position), []).append(position)
        return {
            key: Portfolio(group_positions).total()
            for key, group_positions in groups.items()
        }
