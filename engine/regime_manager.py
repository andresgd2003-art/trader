"""
engine/regime_manager.py
=========================
RegimeManager — Market Regime Alternator.

Evalúa el mercado cada inicio de sesión para decidir qué estrategias
están habilitadas en el día.

LÓGICA:
  BULL  : SPY > SMA200 y VIX < 20  → habilita estrategias 1,2,4,5,8 (momentum)
  BEAR  : SPY < SMA200 y VIX > 25  → habilita estrategias 3,6,9 (mean rev/fade)
  CHOP  : VIX entre 15-25, SPY flat → habilita estrategias 7,10 (pairs/rotation)

Estado global disponible para el dashboard via get_current_regime().
"""

import os
import logging
from enum import Enum
from collections import deque
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    CHOP = "CHOP"
    UNKNOWN = "UNKNOWN"


# Estado global para el dashboard
_CURRENT_REGIME: dict = {
    "regime": Regime.UNKNOWN,
    "spy_price": 0.0,
    "spy_sma200": 0.0,
    "vix_price": 0.0,
    "last_assessed": None,
    "enabled_strategies": [],
}

# Mapa de estrategias habilitadas por régimen
REGIME_STRATEGY_MAP = {
    Regime.BULL:  [1, 2, 4, 5, 8],    # Momentum & Breakouts
    Regime.BEAR:  [3, 6, 9],           # Mean Reversion & Gap Fades
    Regime.CHOP:  [7, 10],             # Pairs & Sector Rotation
    Regime.UNKNOWN: [1, 7, 10],        # Conservador si no hay datos
}


def get_current_regime() -> dict:
    """Retorna el régimen actual (accesible desde el dashboard)."""
    return _CURRENT_REGIME


class RegimeManager:
    """
    Evaluador del régimen de mercado al inicio de cada sesión (~09:00 EST).
    Usa SPY y VIX como proxies del mercado general.
    """

    VIX_BEAR_THRESHOLD = 25.0
    VIX_BULL_THRESHOLD = 20.0
    SMA_PERIOD = 200

    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key
        )
        self.current_regime = Regime.UNKNOWN

    def assess(self) -> Regime:
        """
        Evalúa el régimen del mercado.
        Retorna el Regime actual y actualiza el estado global.
        """
        global _CURRENT_REGIME

        try:
            end = datetime.now()
            start = end - timedelta(days=300)  # 300 días para SMA200

            # Obtener barras diarias de SPY
            spy_bars = self.client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols="SPY",
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                limit=210
            ))

            # Obtener precio de VIX (via VIXY — ETF proxy de VIX en Alpaca)
            vix_bars = self.client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols="VIXY",
                timeframe=TimeFrame.Day,
                start=end - timedelta(days=5),
                end=end,
                limit=5
            ))

            spy_df = spy_bars.df.reset_index()
            spy_close_prices = spy_df[spy_df["symbol"] == "SPY"]["close"].values

            if len(spy_close_prices) < self.SMA_PERIOD:
                logger.warning("[Regime] Datos insuficientes para SMA200. Régimen: UNKNOWN")
                return Regime.UNKNOWN

            spy_price = spy_close_prices[-1]
            spy_sma200 = float(pd.Series(spy_close_prices).rolling(self.SMA_PERIOD).mean().iloc[-1])

            # VIX proxy via VIXY ETF
            vix_df = vix_bars.df.reset_index()
            vixy_prices = vix_df[vix_df["symbol"] == "VIXY"]["close"].values
            vix_proxy = float(vixy_prices[-1]) if len(vixy_prices) > 0 else 20.0

            # Determinar régimen
            if spy_price > spy_sma200 and vix_proxy < self.VIX_BULL_THRESHOLD:
                regime = Regime.BULL
            elif spy_price < spy_sma200 and vix_proxy > self.VIX_BEAR_THRESHOLD:
                regime = Regime.BEAR
            else:
                regime = Regime.CHOP

            self.current_regime = regime
            enabled = REGIME_STRATEGY_MAP[regime]

            # Actualizar estado global
            _CURRENT_REGIME.update({
                "regime": regime.value,
                "spy_price": round(spy_price, 2),
                "spy_sma200": round(spy_sma200, 2),
                "vix_price": round(vix_proxy, 2),
                "last_assessed": datetime.now().isoformat(),
                "enabled_strategies": enabled,
            })

            logger.info(
                f"[Regime] 📊 Régimen: {regime.value} | "
                f"SPY={spy_price:.2f} vs SMA200={spy_sma200:.2f} | "
                f"VIX~{vix_proxy:.2f} | Estrategias activas: {enabled}"
            )

            return regime

        except Exception as e:
            logger.error(f"[Regime] ❌ Error evaluando régimen: {e}")
            return Regime.UNKNOWN

    def is_strategy_enabled(self, strat_number: int) -> bool:
        """Verifica si una estrategia está habilitada en el régimen actual."""
        enabled = REGIME_STRATEGY_MAP.get(self.current_regime, [])
        return strat_number in enabled
