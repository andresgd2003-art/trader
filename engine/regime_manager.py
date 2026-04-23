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
    "spy_sma50": 0.0,
    "spy_sma200": 0.0,
    "vix_price": 0.0,
    "last_assessed": None,
    "enabled_strategies": [],
}

REGIME_ETF_MAP = {
    Regime.BULL:    [1, 2, 4, 5, 8],
    Regime.BEAR:    [3, 6, 9],
    Regime.CHOP:    [7, 10],
    Regime.UNKNOWN: [7, 10],
}
REGIME_CRYPTO_MAP = {
    Regime.BULL:    [1, 2, 4, 8, 9, 11],
    Regime.BEAR:    [5, 6, 10],
    Regime.CHOP:    [3, 7, 11],
    Regime.UNKNOWN: [3, 7, 11],
}
REGIME_EQUITIES_MAP = {
    Regime.BULL:    [2, 4, 5, 8, 9],
    Regime.BEAR:    [10],
    Regime.CHOP:    [10],
    Regime.UNKNOWN: [10],
}

SIZING_BY_REGIME = {"BULL": 0.08, "CHOP": 0.05, "BEAR": 0.03, "UNKNOWN": 0.03}

# Compatibilidad hacia atrás — se mantiene el mapa original
REGIME_STRATEGY_MAP = REGIME_ETF_MAP


def get_current_regime() -> dict:
    """Retorna el régimen actual (accesible desde el dashboard)."""
    return _CURRENT_REGIME


_LAST_HOURLY_ASSESS: dict = {"ts": None}  # Tracker para evaluación horaria


class RegimeManager:
    """
    Evaluador del régimen de mercado al inicio de cada sesión (~09:00 EST).
    Usa SPY y VIX como proxies del mercado general.
    """

    VIX_BEAR_THRESHOLD = 25.0
    VIX_BULL_THRESHOLD = 20.0
    SMA_PERIOD = 200
    SMA_FAST_PERIOD = 50

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
            spy_sma50 = float(pd.Series(spy_close_prices).rolling(self.SMA_FAST_PERIOD).mean().iloc[-1])

            try:
                import yfinance as yf
                vix_hist = yf.Ticker("^VIX").history(period="5d")
                vix_proxy = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else None
            except Exception as e:
                logger.warning(f"[Regime] yfinance falló: {e}")
                vix_proxy = None

            if vix_proxy is None:
                # Fallback: VIXY de Alpaca como proxy direccional
                try:
                    vixy_df = vix_bars.df.reset_index()
                    vixy_closes = vixy_df[vixy_df["symbol"] == "VIXY"]["close"].values
                    if len(vixy_closes) >= 2:
                        change = (vixy_closes[-1] - vixy_closes[0]) / vixy_closes[0]
                        if change > 0.05:   vix_proxy = 27.0
                        elif change < -0.05: vix_proxy = 17.0
                        else:                vix_proxy = 22.0
                        logger.info(f"[Regime] VIX proxy desde VIXY change {change*100:+.1f}% → {vix_proxy}")
                except Exception as e2:
                    logger.error(f"[Regime] VIXY fallback también falló: {e2}")

            if vix_proxy is None:
                logger.error("[Regime] Sin VIX disponible. Retornando UNKNOWN.")
                _CURRENT_REGIME.update({"regime": "UNKNOWN", "last_assessed": datetime.now().isoformat()})
                return Regime.UNKNOWN

            # Como usamos ^VIX real, los threshold originales de VIX (20 y 30) son correctos
            # Determinar régimen dual (SMA50 y SMA200)
            if spy_price > spy_sma50 and spy_price > spy_sma200 and vix_proxy < self.VIX_BULL_THRESHOLD:
                regime = Regime.BULL
            elif spy_price < spy_sma50 and spy_price < spy_sma200 and vix_proxy > self.VIX_BEAR_THRESHOLD:
                regime = Regime.BEAR
            else:
                regime = Regime.CHOP

            self.current_regime = regime
            enabled = REGIME_STRATEGY_MAP[regime]

            # Actualizar estado global
            _CURRENT_REGIME.update({
                "regime": regime.value,
                "spy_price": round(spy_price, 2),
                "spy_sma50": round(spy_sma50, 2),
                "spy_sma200": round(spy_sma200, 2),
                "vix_price": round(vix_proxy, 2),
                "last_assessed": datetime.now().isoformat(),
                "enabled_strategies": enabled,
                "suggested_sizing": SIZING_BY_REGIME[regime.value],
            })

            sizing = "4%" if regime == Regime.BULL else "3%" if regime == Regime.CHOP else "2%"
            logger.info(
                f"[Regime] 📊 Régimen: {regime.value} | "
                f"SPY={spy_price:.2f} vs SMA50={spy_sma50:.2f} vs SMA200={spy_sma200:.2f} | "
                f"VIX~{vix_proxy:.2f} | Sizing activo: {sizing} | Todas activas!"
            )

            return regime

        except Exception as e:
            logger.error(f"[Regime] ❌ Error evaluando régimen: {e}")
            return Regime.UNKNOWN

    def is_strategy_enabled(self, strat_number: int, engine: str = "etf") -> bool:
        """Verifica si una estrategia está habilitada en el régimen actual.
        
        Args:
            strat_number: Número de estrategia (1-10)
            engine: Motor al que pertenece: 'etf', 'crypto', 'equities'
        """
        # Utilizar el estado global en lugar de self.current_regime para forzar sincronía
        regime_str = _CURRENT_REGIME.get("regime", "UNKNOWN")
        try:
            regime = Regime(regime_str)
        except ValueError:
            regime = Regime.UNKNOWN

        if engine == "crypto":
            enabled = REGIME_CRYPTO_MAP.get(regime, [])
        elif engine == "equities":
            enabled = REGIME_EQUITIES_MAP.get(regime, [])
        else:  # etf (default)
            enabled = REGIME_ETF_MAP.get(regime, [])
        return strat_number in enabled

    def assess_if_needed(self) -> Regime:
        """Re-evalúa el régimen solo si ha pasado más de 1 hora desde la última evaluación."""
        from datetime import datetime
        now = datetime.now()
        last = _LAST_HOURLY_ASSESS.get("ts")
        if last is None or (now - last).total_seconds() > 3600:
            regime = self.assess()
            _LAST_HOURLY_ASSESS["ts"] = now
            return regime
        return self.current_regime
