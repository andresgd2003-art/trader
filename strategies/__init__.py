"""
strategies/__init__.py
"""
from .strat_01_macross import GoldenCrossStrategy
from .strat_02_donchian import DonchianBreakoutStrategy
from .strat_03_rotation import MomentumRotationStrategy
from .strat_04_macd import MACDTrendStrategy
from .strat_05_rsi_dip import RSIDipStrategy
from .strat_06_bollinger import BollingerReversionStrategy
from .strat_07_vix_filter import VIXFilteredReversionStrategy
# from .strat_08_vwap import VWAPBounceStrategy
from .strat_09_pairs import PairsTradingStrategy
from .strat_10_grid import GridTradingStrategy

__all__ = [
    "GoldenCrossStrategy",
    "DonchianBreakoutStrategy",
    "MomentumRotationStrategy",
    "MACDTrendStrategy",
    "RSIDipStrategy",
    "BollingerReversionStrategy",
    "VIXFilteredReversionStrategy",
    # "VWAPBounceStrategy",
    "PairsTradingStrategy",
    "GridTradingStrategy",
]
