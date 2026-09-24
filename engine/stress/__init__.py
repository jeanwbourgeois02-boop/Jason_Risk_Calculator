"""The commodity stress (commodity-stress lane): outright, curve-shape, spread, FX and
historical-replay scenarios on the book's commodity futures.

``commodity_stress(conn, as_of, scenarios=None, *, positions=None, spreads=None, history=None)``
is the one entry point (``commodity.py``); the scenarios are plain data in
``config/commodity_stress.yaml``, read and checked by ``scenarios.py``.
"""

from engine.stress.commodity import commodity_stress, default_history
from engine.stress.scenarios import ScenarioError, load_scenarios, validate_scenario, validate_scenarios

__all__ = ["commodity_stress", "default_history", "ScenarioError", "load_scenarios",
           "validate_scenario", "validate_scenarios"]
