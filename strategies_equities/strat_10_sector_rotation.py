"""
strat_10_sector_rotation.py — Relative Strength Sector Rotation
================================================================
Régimen: CHOP | Universo: 11 SPDR Sector ETFs + sus Top 5 Holdings
Timeframe: Weekly bars (viernes al cierre)

Lógica:
  1. Calcular momentum de 3 meses (65 barras diarias) de los 11 ETFs SPDR
  2. Seleccionar los Top 2 sectores con mayor momentum
  3. Dentro de cada sector, comprar las 3 acciones con RSI14 más alto
     (relative strength dentro del sector ganador)

ETFs SPDR:
  XLK (Tech), XLF (Financials), XLV (Health), XLE (Energy),
  XLI (Industrial), XLB (Materials), XLU (Utilities),
  XLRE (Real Estate), XLC (Comm), XLP (Staples), XLY (Discretionary)
"""
import logging
from collections import deque
from datetime import datetime
import pandas as pd
from ta.momentum import RSIIndicator
from engine.base_strategy import BaseStrategy
try:
    from engine.daily_mode import get_active_mode
    from engine.stock_scorer import get_scorer
    _SCORER_AVAILABLE = True
except ImportError:
    _SCORER_AVAILABLE = False
    def get_active_mode(): return "A"

logger = logging.getLogger(__name__)

SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC", "XLP", "XLY"]

# Top 5 holdings por sector (para screening interno)
SECTOR_HOLDINGS = {
    "XLK":  ["MSFT", "AAPL", "NVDA", "AVGO", "ORCL"],
    "XLF":  ["BRK.B", "JPM", "V", "MA", "BAC"],
    "XLV":  ["LLY", "UNH", "JNJ", "ABBV", "MRK"],
    "XLE":  ["XOM", "CVX", "COP", "EOG", "SLB"],
    "XLI":  ["GE", "RTX", "HON", "UNP", "CAT"],
    "XLB":  ["LIN", "SHW", "FCX", "APD", "NEM"],
    "XLU":  ["NEE", "SO", "DUK", "AEP", "SRE"],
    "XLRE": ["PLD", "AMT", "EQIX", "PSA", "O"],
    "XLC":  ["META", "GOOGL", "GOOG", "NFLX", "CHTR"],
    "XLP":  ["PG", "COST", "KO", "PEP", "WMT"],
    "XLY":  ["AMZN", "TSLA", "HD", "MCD", "NKE"],
}

ALL_SYMBOLS = SECTOR_ETFS + list(set([s for holdings in SECTOR_HOLDINGS.values() for s in holdings]))


class SectorRotationStrategy(BaseStrategy):
    STRAT_NUMBER = 10
    MOMENTUM_PERIOD = 65   # 3 meses = ~65 barras diarias
    RSI_PERIOD = 14
    TOP_SECTORS = 2
    TOP_STOCKS = 3

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Sector Rotation",
            symbols=ALL_SYMBOLS,
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes: dict[str, deque] = {s: deque(maxlen=70) for s in ALL_SYMBOLS}
        self._current_positions: dict[str, bool] = {}
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        for sym in ALL_SYMBOLS:
            qty = self.sync_position_from_alpaca(sym)
            if qty > 0:
                self._current_positions[sym] = True
        self._last_friday = -1

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine='equities'):
            return

        self._closes[bar.symbol].append(float(bar.close))

        # Solo ejecutar lógica el viernes al cierre (weekday() == 4)
        now = bar.timestamp if hasattr(bar.timestamp, 'weekday') else datetime.now()
        is_friday = hasattr(now, 'weekday') and now.weekday() == 4
        current_week = now.isocalendar()[1] if hasattr(now, 'isocalendar') else 0

        if not is_friday or current_week == self._last_friday:
            return

        # Verificar que todos los ETFs tengan datos suficientes
        etfs_ready = all(
            len(self._closes.get(etf, [])) >= self.MOMENTUM_PERIOD
            for etf in SECTOR_ETFS
        )
        if not etfs_ready:
            return

        self._last_friday = current_week
        await self._run_rotation()

    async def _run_rotation(self):
        """Ejecuta la rotación semanal de sectores."""
        # Calcular momentum de 3 meses para cada ETF
        sector_momentum = {}
        for etf in SECTOR_ETFS:
            closes = list(self._closes[etf])
            if len(closes) >= self.MOMENTUM_PERIOD:
                mom = (closes[-1] / closes[-self.MOMENTUM_PERIOD] - 1) * 100
                sector_momentum[etf] = mom

        if not sector_momentum:
            return

        # Top 2 sectores por momentum
        top_sectors = sorted(sector_momentum, key=sector_momentum.get, reverse=True)[:self.TOP_SECTORS]
        logger.info(f"[{self.name}] 🌍 Top 2 sectores: {top_sectors} | Momentums: {[f'{sector_momentum[s]:.1f}%' for s in top_sectors]}")

        # Obtener candidatos de los top sectores
        candidates = []

        # ─── Propuesta C (Modo C): pre-filtrar por StockScorer ───────────────
        mode = get_active_mode() if _SCORER_AVAILABLE else "A"
        scorer_whitelist = set()
        if mode == "C" and _SCORER_AVAILABLE:
            try:
                scorer_whitelist = set(get_scorer().get_symbols_above(min_score=60.0))
                logger.info(
                    f"[{self.name}] 🎯 Modo C activo: {len(scorer_whitelist)} acciones con score≥60/100"
                )
            except Exception as e:
                logger.warning(f"[{self.name}] StockScorer no disponible, modo fallback: {e}")
        # ─────────────────────────────────────────────────────────────────────

        for sector in top_sectors:
            holdings = SECTOR_HOLDINGS.get(sector, [])
            for stock in holdings:
                # En Modo C: solo stocks con score ≥ 60 pasan al ranking RSI
                if scorer_whitelist and stock not in scorer_whitelist:
                    logger.debug(f"[{self.name}] 📊 {stock} ignorado (score bajo en Modo C)")
                    continue
                closes = list(self._closes.get(stock, []))
                if len(closes) >= self.RSI_PERIOD + 1:
                    rsi = RSIIndicator(pd.Series(closes), window=self.RSI_PERIOD).rsi().iloc[-1]
                    candidates.append((stock, rsi, closes[-1]))

        if not candidates:
            return

        # Seleccionar Top 3 por RSI14 más alto
        top_stocks = sorted(candidates, key=lambda x: x[1], reverse=True)[:self.TOP_STOCKS]

        # Cerrar posiciones anteriores
        for sym in list(self._current_positions.keys()):
            if sym not in [s[0] for s in top_stocks]:
                logger.info(f"[{self.name}] SALIENDO {sym} (ya no está en top)")
                await self.order_manager.close_position(sym, 1, self.name)
                del self._current_positions[sym]

        # Comprar nuevas selecciones
        for stock, rsi, price in top_stocks:
            if stock not in self._current_positions and price > 0:
                logger.info(f"[{self.name}] 🔄 ROTACIÓN → COMPRANDO {stock} RSI={rsi:.1f} @ ${price:.2f}")
                await self.order_manager.buy_bracket(
                    symbol=stock,
                    price=price,
                    stop_loss_pct=0.08,
                    take_profit_pct=0.15,
                    strategy_name=self.name
                )
                self._current_positions[stock] = True
