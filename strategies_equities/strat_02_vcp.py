"""
strat_02_vcp.py — Volatility Contraction Pattern (Minervini)
=============================================================
Régimen: BULL | Universo: Top 100 High Beta stocks (lista fija curada)
Timeframe: Daily bars | Lógica: Minervini VCP

Lógica:
  1. Stock debe estar por encima de SMA50, SMA150, SMA200 (tendencia alcista)
  2. En las últimas 3 semanas, el rango High-Low se estrecha (contracción)
  3. El volumen se "seca" durante la contracción (<50% del promedio)
  4. Entry: breakout por encima de la resistencia local con Vol >150%
"""
import logging
from collections import deque
import pandas as pd
from ta.trend import EMAIndicator, SMAIndicator
from engine.base_strategy import BaseStrategy
try:
    from engine.daily_mode import get_active_mode
    from engine.stock_scorer import get_scorer
    _SCORER_AVAILABLE = True
except ImportError:
    _SCORER_AVAILABLE = False
    def get_active_mode(): return "A"

logger = logging.getLogger(__name__)

# Universo de High Beta stocks (estático, actualizar mensualmente)
HIGH_BETA_UNIVERSE = [
    "NVDA", "AMD", "MARA", "RIOT", "TSLA", "PLTR", "SOFI",
    "RIVN", "LCID", "MVIS", "GME", "AMC", "BBBY", "SNDL",
    "ATER", "CLOV", "WKHS", "NKLA", "IDEX", "CEI"
]


class VCPStrategy(BaseStrategy):
    STRAT_NUMBER = 2
    SMA50_PERIOD = 50
    SMA150_PERIOD = 150
    SMA200_PERIOD = 200
    VCP_WEEKS = 3        # 15 días de datos para VCP
    VOL_DRY_PCT = 0.50   # Volumen seco <50% del promedio
    BREAKOUT_VOL = 1.50  # Breakout requiere >150% del vol promedio
    SCORE_MIN_C = 55.0   # Umbral de score mínimo en Modo C (menos estricto que rotation)

    def __init__(self, order_manager, regime_manager=None):
        # En Modo C se amplia el universo con scores > 55; fallback al estático
        initial_universe = self._get_universe_for_mode()
        super().__init__(
            name="VCP Minervini",
            symbols=initial_universe,
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._universe = initial_universe
        self._closes: dict[str, deque] = {s: deque(maxlen=210) for s in self._universe}
        self._highs: dict[str, deque] = {s: deque(maxlen=30) for s in self._universe}
        self._lows: dict[str, deque] = {s: deque(maxlen=30) for s in self._universe}
        self._volumes: dict[str, deque] = {s: deque(maxlen=30) for s in self._universe}
        self._traded_today: set = set()

    @staticmethod
    def _get_universe_for_mode() -> list:
        """Retorna el universo de acciones según el modo activo.
        Modo C: usa el top del scorer + HIGH_BETA_UNIVERSE como fallback.
        Otros modos: usa HIGH_BETA_UNIVERSE estático.
        """
        if not _SCORER_AVAILABLE:
            return list(HIGH_BETA_UNIVERSE)
        try:
            mode = get_active_mode()
            if mode == "C":
                scored = get_scorer().get_symbols_above(min_score=55.0)
                if scored:
                    # Combinar el universo scorado + HIGH_BETA por si se solapan
                    combined = list(dict.fromkeys(scored + HIGH_BETA_UNIVERSE))  # preserva orden
                    logger.info(f"[VCP] 🎯 Modo C: universo ampliado a {len(combined)} acciones")
                    return combined
        except Exception:
            pass
        return list(HIGH_BETA_UNIVERSE)

    def update_symbols(self, new_symbols: list):
        self.symbols = new_symbols
        self._traded_today = set()
        self._daily_smas = {}
        logger.info(f"[{self.name}] Calculando SMA200 diarias para {len(new_symbols)} símbolos...")
        
        try:
            import yfinance as yf
            import pandas as pd
            if not getattr(self, '_daily_data_fetched', False) and self.symbols:
                data = yf.download(self.symbols, period="300d", group_by='ticker', threads=True, progress=False)
                count = 0
                for sym in self.symbols:
                    try:
                        df = data if len(self.symbols) == 1 else data.get(sym)
                        if df is not None and not df.empty and len(df) >= 200:
                            sma50 = df['Close'].rolling(50).mean().iloc[-1]
                            sma150 = df['Close'].rolling(150).mean().iloc[-1]
                            sma200 = df['Close'].rolling(200).mean().iloc[-1]
                            self._daily_smas[sym] = {"sma50": float(sma50), "sma150": float(sma150), "sma200": float(sma200)}
                            count += 1
                    except:
                        pass
                logger.info(f"[{self.name}] ✅ Se calcularon SMAs diarias reales para {count} símbolos.")
                self._daily_data_fetched = True
        except Exception as e:
            logger.error(f"[{self.name}] Error descargando datos diarios: {e}")

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine='equities'):
            return
        if bar.symbol in self._traded_today:
            return

        sym = bar.symbol
        c, h, l, v = float(bar.close), float(bar.high), float(bar.low), float(bar.volume)

        self._closes[sym].append(c)
        self._highs[sym].append(h)
        self._lows[sym].append(l)
        self._volumes[sym].append(v)

        # Usar las SMAs Diarias pre-calculadas en update_symbols en lugar de minutos
        daily_smas = getattr(self, "_daily_smas", {}).get(sym)
        if not daily_smas:
            return  # No hay datos diarios suficientes para verificar la tendencia a largo plazo

        sma50  = daily_smas["sma50"]
        sma150 = daily_smas["sma150"]
        sma200 = daily_smas["sma200"]

        # 1. Verificar tendencia alcista (Stage 2)
        if not (c > sma50 > sma150 > sma200):
            return

        # 2. Verificar VCP: rango H-L se estrecha en últimas 3 semanas
        recent_highs = list(self._highs[sym])[-self.VCP_WEEKS * 5:]
        recent_lows  = list(self._lows[sym])[-self.VCP_WEEKS * 5:]
        recent_vols  = list(self._volumes[sym])[-self.VCP_WEEKS * 5:]

        if len(recent_highs) < 10:
            return

        ranges = [recent_highs[i] - recent_lows[i] for i in range(len(recent_highs))]
        # Rango se estrecha si la última semana < primera semana
        first_week_range = sum(ranges[:5]) / 5
        last_week_range  = sum(ranges[-5:]) / 5

        if last_week_range >= first_week_range:
            return  # No hay contracción

        # 3. Volumen seco en la última semana
        vol_avg_full = sum(recent_vols) / len(recent_vols)
        vol_avg_last = sum(recent_vols[-5:]) / 5

        if vol_avg_last > vol_avg_full * self.VOL_DRY_PCT:
            return  # No hay dry-up de volumen

        # 4. Breakout: precio por encima del high de las últimas 3 semanas con volumen
        resistance = max(recent_highs[:-1])  # Resistencia = max excluyendo la barra actual

        if c > resistance and v > vol_avg_full * self.BREAKOUT_VOL:
            # En Modo C: anotar el score en el log para trazabilidad
            score_note = ""
            if _SCORER_AVAILABLE and get_active_mode() == "C":
                try:
                    top = {s["symbol"]: s["score"] for s in get_scorer().get_top_scores(50)}
                    sc = top.get(sym, "n/a")
                    score_note = f" | Score: {sc}/100"
                except Exception:
                    pass
            
            # ⚠️ ANTI-DUPLICADO: Verificar posición viva para no re-entrar si reinició hoy
            if self.sync_position_from_alpaca(sym) > 0:
                logger.info(f"[{self.name}] ⚠️ Breakout en {sym} pero ya hay posición activa. Evitando duplicado.")
                self._traded_today.add(sym)
                return

            logger.info(
                f"[{self.name}] 🏔️ VCP BREAKOUT {sym}! "
                f"Close={c:.2f} > Resistance={resistance:.2f} | Vol={v:.0f} ({v/vol_avg_full:.1f}x){score_note}"
            )
            await self.order_manager.buy_bracket(
                symbol=sym,
                price=c,
                stop_loss_pct=0.07,   # Stop debajo del pivot (7% para acciones de mayor precio)
                take_profit_pct=0.20,  # VCP suele dar 20%+ moves
                strategy_name=self.name
            )
            self._traded_today.add(sym)

    def on_market_open(self):
        self._traded_today = set()
