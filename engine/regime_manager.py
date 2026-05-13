"""
engine/regime_manager.py
=========================
RegimeManager — Market Regime Alternator.

PROPUESTA 2 (2026-05-13): ADX + Estructura VIX (VIX9D/VIX3M) + Fear & Greed para crypto
PROPUESTA 3 (2026-05-13): Confidence Score [0.15-1.0] que modula el sizing proporcionalmente

LÓGICA (con histéresis anti-whipsaw):
  BULL  : SPY > SMA50(diario) y VIX < 16 (entry) / VIX > 20 (exit)
  BEAR  : SPY < SMA50(diario) y VIX > 25 (entry) / VIX < 22 (exit)
  CHOP  : todo lo demás (catch-all)
"""

import os
import logging
import requests
from enum import Enum
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


_CURRENT_REGIME: dict = {
    "regime": Regime.UNKNOWN,
    "spy_price": 0.0,
    "spy_sma20": 0.0,
    "spy_sma50": 0.0,
    "vix_price": 0.0,
    "adx_value": None,
    "vix9d": None,
    "vix3m": None,
    "vix_structure": "unknown",
    "confidence_score": 0.5,
    "crypto_confidence_score": 0.5,
    "fear_greed_value": None,
    "last_assessed": None,
    "enabled_strategies": [],
}

REGIME_ETF_MAP = {
    Regime.BULL:    [1, 2, 4, 5, 8],
    Regime.BEAR:    [3, 6, 9, 11],
    Regime.CHOP:    [7, 10, 11],
    Regime.UNKNOWN: [5, 6, 7, 8, 10],
}
REGIME_CRYPTO_MAP = {
    Regime.BULL:    [1, 2, 4, 8, 9, 11, 12],
    Regime.BEAR:    [5, 6, 10, 12],
    Regime.CHOP:    [3, 7, 11, 12],
    Regime.UNKNOWN: [3, 7, 11, 12],
}
REGIME_EQUITIES_MAP = {
    Regime.BULL:    [2],
    Regime.BEAR:    [4],
    Regime.CHOP:    [4],
    Regime.UNKNOWN: [],
}

SIZING_BY_REGIME = {"BULL": 0.08, "CHOP": 0.05, "BEAR": 0.03, "UNKNOWN": 0.03}
REGIME_STRATEGY_MAP = REGIME_ETF_MAP  # backward compat


def get_current_regime() -> dict:
    return _CURRENT_REGIME


_LAST_HOURLY_ASSESS: dict = {"ts": None}


class RegimeManager:
    """
    Evaluador del régimen de mercado con histéresis, ADX, VIX structure y Fear & Greed.
    """

    VIX_BULL_ENTRY = 16.0
    VIX_BULL_EXIT  = 20.0
    VIX_BEAR_ENTRY = 25.0
    VIX_BEAR_EXIT  = 22.0

    SMA_PERIOD      = 50
    SMA_FAST_PERIOD = 20
    ADX_PERIOD      = 14
    ADX_TREND_THRESHOLD = 25.0
    ADX_STRONG_TREND    = 35.0

    def __init__(self):
        self.api_key    = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.client     = StockHistoricalDataClient(
            api_key=self.api_key, secret_key=self.secret_key
        )
        self.current_regime = Regime.UNKNOWN

    @staticmethod
    def _is_market_hours() -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        return 9 <= now.hour < 17

    def _compute_confidence_score(
        self,
        regime: Regime,
        adx_value: float,
        vix_ratio: float,
        spy_price: float,
        spy_sma20: float,
        spy_sma50: float,
    ) -> float:
        """
        Score de confianza [0.15-1.0] para ETF/Equities.
        Combina ADX (fuerza de tendencia) y ratio VIX9D/VIX3M (estructura de volatilidad).
        """
        score = 0.50

        if regime == Regime.BULL:
            if adx_value >= self.ADX_STRONG_TREND:
                score += 0.25
            elif adx_value >= self.ADX_TREND_THRESHOLD:
                score += 0.15
            else:
                score -= 0.20
            if vix_ratio < 0.85:
                score += 0.20
            elif vix_ratio < 1.0:
                score += 0.05
            else:
                score -= 0.15
            if spy_price > spy_sma20 > spy_sma50:
                score += 0.05

        elif regime == Regime.BEAR:
            if adx_value >= self.ADX_TREND_THRESHOLD:
                score += 0.20
            else:
                score -= 0.15
            if vix_ratio >= 1.15:
                score += 0.25
            elif vix_ratio >= 1.0:
                score += 0.10
            else:
                score -= 0.10

        elif regime == Regime.CHOP:
            if adx_value < 20:
                score += 0.25
            elif adx_value < self.ADX_TREND_THRESHOLD:
                score += 0.10
            else:
                score -= 0.20
            if 0.90 <= vix_ratio <= 1.05:
                score += 0.10

        return round(max(0.15, min(1.0, score)), 3)

    def _compute_crypto_confidence(
        self,
        regime: Regime,
        fg_value,
    ) -> float:
        """
        Score de confianza [0.15-1.0] para Crypto usando Fear & Greed Index.
        0-25=Extreme Fear, 25-45=Fear, 45-55=Neutral, 55-75=Greed, 75-100=Extreme Greed
        """
        if fg_value is None:
            return 0.5

        score = 0.50

        if regime == Regime.BULL:
            if fg_value >= 75:    score += 0.30
            elif fg_value >= 55:  score += 0.20
            elif fg_value >= 45:  score += 0.00
            elif fg_value >= 25:  score -= 0.15
            else:                 score -= 0.30

        elif regime == Regime.BEAR:
            if fg_value <= 25:    score += 0.30
            elif fg_value <= 45:  score += 0.20
            elif fg_value <= 55:  score += 0.00
            elif fg_value <= 75:  score -= 0.15
            else:                 score -= 0.30

        elif regime == Regime.CHOP:
            if 40 <= fg_value <= 60:   score += 0.20
            elif 30 <= fg_value <= 70: score += 0.05
            else:                      score -= 0.10

        return round(max(0.15, min(1.0, score)), 3)

    @staticmethod
    def _get_fear_greed():
        """Obtiene Fear & Greed Index de alternative.me. Retorna int 0-100 o None."""
        try:
            resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if data:
                return int(data[0]["value"])
        except Exception as e:
            logger.warning(f"[Regime] Fear & Greed API falló: {e}")
        return None

    def assess(self) -> Regime:
        """
        Evalúa el régimen del mercado usando barras DIARIAS de SPY.
        Calcula ADX, estructura VIX, Fear & Greed y confidence scores.
        """
        global _CURRENT_REGIME

        try:
            end   = datetime.now()
            start = end - timedelta(days=365)

            from alpaca.data.enums import DataFeed

            def _get_daily_bars(symbol, feed=DataFeed.IEX):
                try:
                    return self.client.get_stock_bars(StockBarsRequest(
                        symbol_or_symbols=symbol,
                        timeframe=TimeFrame.Day,
                        start=start, end=end, feed=feed,
                    ))
                except Exception as e:
                    if "SIP" in str(e) or "subscription" in str(e).lower():
                        if feed != DataFeed.IEX:
                            return _get_daily_bars(symbol, feed=DataFeed.IEX)
                    raise

            # ── 1. SPY barras diarias (close + high + low) ──
            spy_bars = _get_daily_bars("SPY")
            spy_df   = spy_bars.df.reset_index()
            spy_spy  = spy_df[spy_df["symbol"] == "SPY"]

            spy_close = spy_spy["close"].values
            spy_high  = spy_spy["high"].values  if "high"  in spy_spy.columns else None
            spy_low   = spy_spy["low"].values   if "low"   in spy_spy.columns else None

            if len(spy_close) < self.SMA_PERIOD:
                _last = float(spy_close[-1]) if len(spy_close) > 0 else 0.0
                _CURRENT_REGIME.update({
                    "regime": Regime.UNKNOWN.value, "spy_price": round(_last, 2),
                    "adx_value": None, "vix_structure": "unknown",
                    "confidence_score": 0.5, "crypto_confidence_score": 0.5,
                    "last_assessed": datetime.now().isoformat(),
                    "enabled_strategies": REGIME_STRATEGY_MAP[Regime.UNKNOWN],
                    "suggested_sizing": SIZING_BY_REGIME["UNKNOWN"],
                })
                return Regime.UNKNOWN

            spy_price = float(spy_close[-1])
            spy_sma50 = float(pd.Series(spy_close).rolling(self.SMA_PERIOD).mean().iloc[-1])
            spy_sma20 = float(pd.Series(spy_close).rolling(self.SMA_FAST_PERIOD).mean().iloc[-1])

            # ── 2. ADX ──
            adx_value = None
            if spy_high is not None and spy_low is not None:
                try:
                    from ta.trend import ADXIndicator
                    adx_ind = ADXIndicator(
                        high=pd.Series(spy_high), low=pd.Series(spy_low),
                        close=pd.Series(spy_close), window=self.ADX_PERIOD, fillna=True
                    )
                    adx_raw = adx_ind.adx().iloc[-1]
                    if pd.notna(adx_raw):
                        adx_value = round(float(adx_raw), 2)
                except Exception as adx_err:
                    logger.warning(f"[Regime] ADX error: {adx_err}")

            # ── 3. VIX principal ──
            vix_proxy = None
            try:
                import yfinance as yf
                vix_hist = yf.Ticker("^VIX").history(period="5d", interval="1d")
                if not vix_hist.empty:
                    vix_proxy = float(vix_hist["Close"].iloc[-1])
            except Exception as e:
                logger.warning(f"[Regime] yfinance VIX falló: {e}")

            if vix_proxy is None:
                try:
                    vixy_bars = _get_daily_bars("VIXY")
                    vixy_df   = vixy_bars.df.reset_index()
                    vixy_cls  = vixy_df[vixy_df["symbol"] == "VIXY"]["close"].values
                    if len(vixy_cls) >= 2:
                        change    = (vixy_cls[-1] - vixy_cls[0]) / vixy_cls[0]
                        vix_proxy = max(10.0, min(40.0, 19.0 + (change * 100) * 0.5))
                except Exception as e2:
                    logger.error(f"[Regime] VIXY fallback falló: {e2}")

            if vix_proxy is None:
                prev_regime = self.current_regime
                if prev_regime != Regime.UNKNOWN:
                    _CURRENT_REGIME.update({"last_assessed": datetime.now().isoformat()})
                    return prev_regime
                _CURRENT_REGIME.update({"regime": "UNKNOWN", "last_assessed": datetime.now().isoformat()})
                return Regime.UNKNOWN

            # ── 4. Estructura VIX (VIX9D/VIX3M) ──
            vix9d, vix3m = None, None
            try:
                import yfinance as yf
                vix9d = float(yf.Ticker("^VIX9D").history(period="3d", interval="1d")["Close"].iloc[-1])
                vix3m = float(yf.Ticker("^VIX3M").history(period="3d", interval="1d")["Close"].iloc[-1])
            except Exception:
                pass

            vix_ratio     = (vix9d / vix3m) if (vix9d and vix3m and vix3m > 0) else 1.0
            vix_structure = "backwardation" if vix_ratio > 1.0 else "contango"

            # ── 5. Clasificación con histéresis ──
            prev = self.current_regime
            spy_above = spy_price > spy_sma50
            spy_below = spy_price < spy_sma50

            if prev == Regime.BULL:
                if vix_proxy > self.VIX_BULL_EXIT or spy_below:
                    regime = Regime.BEAR if (vix_proxy > self.VIX_BEAR_ENTRY and spy_below) else Regime.CHOP
                else:
                    regime = Regime.BULL
            elif prev == Regime.BEAR:
                if vix_proxy < self.VIX_BEAR_EXIT or spy_above:
                    regime = Regime.BULL if (vix_proxy < self.VIX_BULL_ENTRY and spy_above) else Regime.CHOP
                else:
                    regime = Regime.BEAR
            else:
                if spy_above and vix_proxy < self.VIX_BULL_ENTRY:
                    regime = Regime.BULL
                elif spy_below and vix_proxy > self.VIX_BEAR_ENTRY:
                    regime = Regime.BEAR
                else:
                    regime = Regime.CHOP

            if prev != regime and prev != Regime.UNKNOWN:
                logger.info(f"[Regime] 🔄 {prev.value} → {regime.value} (VIX={vix_proxy:.2f})")

            # ── 6. Confidence Scores ──
            effective_adx   = adx_value if adx_value is not None else self.ADX_TREND_THRESHOLD
            confidence_etf  = self._compute_confidence_score(
                regime=regime, adx_value=effective_adx, vix_ratio=vix_ratio,
                spy_price=spy_price, spy_sma20=spy_sma20, spy_sma50=spy_sma50
            )
            fg_value          = self._get_fear_greed()
            confidence_crypto = self._compute_crypto_confidence(regime=regime, fg_value=fg_value)

            # ── 7. Actualizar estado global ──
            self.current_regime = regime
            enabled_etf    = REGIME_ETF_MAP.get(regime, [])
            enabled_eq     = REGIME_EQUITIES_MAP.get(regime, [])
            enabled_crypto = REGIME_CRYPTO_MAP.get(regime, [])
            sizing_pct     = SIZING_BY_REGIME[regime.value]

            _CURRENT_REGIME.update({
                "regime":                regime.value,
                "spy_price":             round(spy_price, 2),
                "spy_sma20":             round(spy_sma20, 2),
                "spy_sma50":             round(spy_sma50, 2),
                "vix_price":             round(vix_proxy, 2),
                "adx_value":             adx_value,
                "vix9d":                 round(vix9d, 2) if vix9d else None,
                "vix3m":                 round(vix3m, 2) if vix3m else None,
                "vix_structure":         vix_structure,
                "confidence_score":      confidence_etf,
                "crypto_confidence_score": confidence_crypto,
                "fear_greed_value":      fg_value,
                "last_assessed":         datetime.now().isoformat(),
                "enabled_strategies":    enabled_etf,
                "suggested_sizing":      sizing_pct,
            })

            adx_str = f"ADX={adx_value:.1f}" if adx_value else "ADX=N/A"
            fg_str  = f"F&G={fg_value}" if fg_value is not None else "F&G=N/A"
            logger.info(
                f"[Regime] 📊 {regime.value} | SPY={spy_price:.2f} SMA50={spy_sma50:.2f} "
                f"VIX={vix_proxy:.2f} ({vix_structure}) | {adx_str} | {fg_str} | "
                f"Conf ETF={confidence_etf:.2f} Crypto={confidence_crypto:.2f} | "
                f"Sizing={sizing_pct*100:.0f}% | "
                f"ETF:{enabled_etf} EQ:{enabled_eq} Crypto:{enabled_crypto}"
            )
            return regime

        except Exception as e:
            logger.error(f"[Regime] ❌ Error: {e}")
            _CURRENT_REGIME.update({
                "regime": Regime.UNKNOWN.value,
                "last_assessed": datetime.now().isoformat(),
                "enabled_strategies": REGIME_STRATEGY_MAP[Regime.UNKNOWN],
                "suggested_sizing": SIZING_BY_REGIME["UNKNOWN"],
                "confidence_score": 0.5,
                "crypto_confidence_score": 0.5,
            })
            return Regime.UNKNOWN

    def is_strategy_enabled(self, strat_number: int, engine: str = "etf") -> bool:
        regime_str = _CURRENT_REGIME.get("regime", "UNKNOWN")
        try:
            regime = Regime(regime_str)
        except ValueError:
            regime = Regime.UNKNOWN
        if engine == "crypto":
            enabled = REGIME_CRYPTO_MAP.get(regime, [])
        elif engine == "equities":
            enabled = REGIME_EQUITIES_MAP.get(regime, [])
        else:
            enabled = REGIME_ETF_MAP.get(regime, [])
        return strat_number in enabled

    def assess_if_needed(self) -> Regime:
        now = datetime.now()
        if not self._is_market_hours():
            return self.current_regime
        last = _LAST_HOURLY_ASSESS.get("ts")
        if last is None or (now - last).total_seconds() > 300:
            regime = self.assess()
            _LAST_HOURLY_ASSESS["ts"] = now
            return regime
        return self.current_regime
