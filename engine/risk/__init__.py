"""Risk metrics for the Risk tab: the nm-dashboard's blended vol, one-day 95 % VaR and
worst-day figures (raw and ex shocks) on this book's own delta, plus the scenario stress,
for the whole book and per underlyer. `metrics.py` has the definitions and the output
shape, `history.py` the market history they read (a risk input only, never a mark
source), `config.py` the parameters (`config/risk.yaml`)."""
from .config import DEFAULTS, load_config
from .history import History, load_history
from .metrics import book_risk

__all__ = ["book_risk", "load_history", "load_config", "History", "DEFAULTS"]
