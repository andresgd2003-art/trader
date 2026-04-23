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
    from engine.stock_scorer import get_scorer
    _SCORER_AVAILABLE = True
except ImportError:
    _SCORER_AVAILABLE = False

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
        initial_universe = self._get_universe()
        super().__init__(
            name="VCP Minervini",
            symbols=initial_universe,
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._universe = initial_universe
        self._volumes: dict[str, deque] = {s: deque(maxlen=60) for s in self._universe}
        self._traded_today: set = set()
        self._vcp_candidates: dict = {}

    @staticmethod
    def _get_universe() -> list:
        """Retorna el universo de acciones usando scorer + HIGH_BETA_UNIVERSE."""
        if not _SCORER_AVAILABLE:
            return list(HIGH_BETA_UNIVERSE)
        try:
            scored = get_scorer().get_symbols_above(min_score=55.0)
            if scored:
                combined = list(dict.fromkeys(scored + HIGH_BETA_UNIVERSE))
                logger.info(f"[VCP] Universo ampliado a {len(combined)} acciones via scorer")
                return combined
        except Exception:
            pass
        return list(HIGH_BETA_UNIVERSE)

    def update_symbols(self, new_symbols: list):
        self.symbols = new_symbols
        self._traded_today = set()
        self._vcp_candidates = {}
        logger.info(f"[{self.name}] Buscando candidatos VCP diarios (3 semanas) en {len(new_symbols)} símbolos...")
        
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
                            
                            c = df['Close'].iloc[-1]
                            
                            # 1. Filtro Macroeconómico (Tendencia)
                            if c > sma50 > sma150 > sma200:
                                recent_df = df.iloc[-15:] # Últimas 3 semanas
                                if len(recent_df) >= 15:
                                    highs = recent_df['High'].values
                                    lows = recent_df['Low'].values
                                    vols = recent_df['Volume'].values
                                    
                                    ranges = highs - lows
                                    first_week_range = sum(ranges[:5]) / 5
                                    last_week_range = sum(ranges[-5:]) / 5
                                    
                                    vol_avg_full = sum(vols) / len(vols)
                                    vol_avg_last = sum(vols[-5:]) / 5
                                    
                                    # 2. Contracción de Volatility y Secado de Volúmen
                                    if last_week_range < first_week_range and vol_avg_last < vol_avg_full * self.VOL_DRY_PCT:
                                        resistance = max(highs)
                                        self._vcp_candidates[sym] = {"resistance": resistance}
                                        count += 1
                    except Exception as loop_e:
                        pass
                logger.info(f"[{self.name}] ✅ {count} símbolos pasaron el filtro Macro VCP. Listos para cazar breakouts.")
                self._daily_data_fetched = True
                
                if self._vcp_candidates:
                    logger.debug(f"[VCP] Candidatos y sus resistencias: {self._vcp_candidates}")
        except Exception as e:
            logger.error(f"[{self.name}] Error descargando/procesando datos macro diarios: {e}")

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="equities"):
            return
        if bar.symbol in self._traded_today:
            return

        sym = bar.symbol
        if sym not in self._vcp_candidates:
            return  # No es candidato macroeconómico del día

        c, v = float(bar.close), float(bar.volume)
        
        if sym not in self._volumes:
            self._volumes[sym] = deque(maxlen=60)
        self._volumes[sym].append(v)

        resistance = self._vcp_candidates[sym]["resistance"]

        # 4. Breakout: precio rompe la resistencia con expansión de volumen intradía
        if c > resistance:
            if len(self._volumes[sym]) > 5:
                # Comprar volumen relativo intradía (las últimas barras excluyendo la actual)
                recent_vols = list(self._volumes[sym])[:-1]
                avg_min_vol = sum(recent_vols) / len(recent_vols)
                
                # Se requiere un pico de volumen relativo en minuto real
                if v < avg_min_vol * self.BREAKOUT_VOL:
                    return
            
            # En Modo C: anotar el score en el log para trazabilidad
            score_note = ""
            if _SCORER_AVAILABLE:
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
                f"[{self.name}] 🏔️ VCP BREAKOUT INTRA-DAY {sym}! "
                f"Close={c:.2f} > Res={resistance:.2f} | Vol={v:.0f}x{score_note}"
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
