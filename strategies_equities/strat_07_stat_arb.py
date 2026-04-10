"""
strat_07_stat_arb.py — Statistical Pairs Trading
================================================
Régimen: CHOP | Universo: Pares pre-definidos co-integrados
Timeframe: 15-min bars

Pares (co-integración clásica verificada):
  KO/PEP, V/MA, HD/LOW, XOM/CVX, JPM/BAC

Lógica Z-Score:
  1. Calcular spread = PriceA - (beta * PriceB)
  2. Calcular Z-Score del spread sobre ventana de 20 días (rolling)
  3. Si Z > +2.0 → A está cara vs B → SHORT A / LONG B
  4. Si Z < -2.0 → A está barata vs B → LONG A / SHORT B
  5. Exit: Z vuelve a 0 (convergencia) o reversal fuerte
"""
import logging
from collections import deque
import pandas as pd
from scipy import stats
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# Pares co-integrados clásicos (A, B)
PAIRS = [
    ("KO", "PEP"),
    ("V", "MA"),
    ("HD", "LOW"),
    ("XOM", "CVX"),
    ("JPM", "BAC"),
]

ZSCORE_ENTRY = 2.0
ZSCORE_EXIT  = 0.5
SPREAD_WINDOW = 60  # 60 barras de 15min ≈ 3 días


class StatArbStrategy(BaseStrategy):
    STRAT_NUMBER = 7

    def __init__(self, order_manager, regime_manager=None):
        all_symbols = list(set([s for pair in PAIRS for s in pair]))
        super().__init__(
            name="Statistical Pairs Arb",
            symbols=all_symbols,
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._prices: dict[str, deque] = {s: deque(maxlen=SPREAD_WINDOW + 10) for s in all_symbols}
        self._positions: dict[tuple, str] = {}  # pair → "long_A" | "long_B" | None
        self._position_qty: dict[tuple, int] = {}
        self._last_minute: int = -1

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER):
            return

        # Control de timeframe 15min
        minute = bar.timestamp.minute if hasattr(bar.timestamp, 'minute') else 0
        is_15m = minute % 15 == 0 and minute != self._last_minute

        self._prices[bar.symbol].append(float(bar.close))

        if not is_15m:
            return
        self._last_minute = minute

        # Evaluar cada par
        for pair in PAIRS:
            sym_a, sym_b = pair
            prices_a = list(self._prices.get(sym_a, []))
            prices_b = list(self._prices.get(sym_b, []))

            if len(prices_a) < SPREAD_WINDOW or len(prices_b) < SPREAD_WINDOW:
                continue

            pa = pd.Series(prices_a[-SPREAD_WINDOW:])
            pb = pd.Series(prices_b[-SPREAD_WINDOW:])

            # OLS: A = alpha + beta * B
            slope, intercept, _, _, _ = stats.linregress(pb, pa)
            spread = pa - (slope * pb + intercept)

            zscore = (spread.iloc[-1] - spread.mean()) / (spread.std() + 1e-9)
            current_a = prices_a[-1]
            current_b = prices_b[-1]

            current_pos = self._positions.get(pair)

            # ── Exit ──
            if current_pos and abs(zscore) < ZSCORE_EXIT:
                qty = self._position_qty.get(pair, 1)
                logger.info(f"[{self.name}] EXIT par {sym_a}/{sym_b} Z={zscore:.2f}")
                await self.order_manager.close_position(sym_a, qty, self.name)
                await self.order_manager.close_position(sym_b, qty, self.name)
                self._positions[pair] = None
                continue

            if current_pos:
                continue

            # ── Entry ──
            if zscore > ZSCORE_ENTRY:
                # A cara vs B → Short A, Long B
                logger.info(f"[{self.name}] 📊 Z={zscore:.2f} → SHORT {sym_a} / LONG {sym_b}")
                await self.order_manager.sell_short(
                    sym_a, current_a, 0.05, 0.08, self.name, 50.0
                )
                await self.order_manager.buy_bracket(
                    sym_b, current_b, 0.05, 0.08, self.name, 50.0
                )
                self._positions[pair] = "short_A"
                self._position_qty[pair] = self.order_manager._calculate_qty(50.0, current_b)

            elif zscore < -ZSCORE_ENTRY:
                # A barata vs B → Long A, Short B
                logger.info(f"[{self.name}] 📊 Z={zscore:.2f} → LONG {sym_a} / SHORT {sym_b}")
                await self.order_manager.buy_bracket(
                    sym_a, current_a, 0.05, 0.08, self.name, 50.0
                )
                await self.order_manager.sell_short(
                    sym_b, current_b, 0.05, 0.08, self.name, 50.0
                )
                self._positions[pair] = "long_A"
                self._position_qty[pair] = self.order_manager._calculate_qty(50.0, current_a)

    def on_market_open(self):
        self._positions = {}
        self._position_qty = {}
        self._last_minute = -1
