"""
strategies/strat_09_pairs.py — Pairs Trading (QQQ ↔ PSQ) — Cash Account Compatible
====================================================================================
LÓGICA ADAPTADA (Long-Only, Sin Shorts):
  Sustituye los shorts por ETFs inversos para compatibilidad con Cash Account.
  
  Par: QQQ (Nasdaq 100) ↔ PSQ (ProShares Short QQQ, -1x inverso)
  
  - Calcula el Z-Score de QQQ sobre su media de 20 períodos (1 período = 5 min)
  - Si Z > 2.0:  QQQ sobrevalorado → Compra PSQ (beneficia de caída de QQQ)
  - Si Z < -2.0: QQQ infravalorado → Compra QQQ (beneficia de subida)
  - Si |Z| < 0.5 con posición abierta → Cierra la posición activa
  
  Solo UNA posición activa a la vez (QQQ o PSQ, nunca ambas).
  
CPU OPTIMIZATION:
  - Logging cada 5 minutos (no cada barra de 1 min)
  - Acumulación de barras cada 5 minutos (reduce cálculos 5x)
"""
import logging
import time
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class PairsTradingStrategy(BaseStrategy):

    SYMBOL_LONG  = "QQQ"   # Activo principal
    SYMBOL_HEDGE = "PSQ"   # ETF Inverso -1x de QQQ (compra = short sintético)
    LOOKBACK     = 20      # Períodos para Z-Score (20 x 5min = 100 min de historia)
    Z_ENTRY      = 2.0     # Umbral de entrada
    Z_EXIT       = 0.5     # Umbral de cierre
    BAR_AGGREGATE = 5      # Agregar N barras de 1-min en 1 barra de 5-min
    LOG_INTERVAL  = 300    # Loguear cada 300 segundos (5 min)

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Pairs Trading",
            symbols=[self.SYMBOL_LONG, self.SYMBOL_HEDGE],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._prices_buffer: list = []       # Buffer de cierre de 1-min para agregar
        self._aggregated = deque(maxlen=self.LOOKBACK)  # Precios agregados de 5-min
        self._position_type = None           # "long_qqq" | "long_psq" | None
        self._last_log_time = 0

        # Restaurar estado real desde Alpaca al reiniciar
        self._restore_state()

    def _restore_state(self):
        """Sincroniza _position_type con las posiciones reales en Alpaca al arrancar."""
        try:
            qty_long = self.sync_position_from_alpaca(self.SYMBOL_LONG)
            qty_hedge = self.sync_position_from_alpaca(self.SYMBOL_HEDGE)

            if qty_long > 0:
                self._position_type = "long_qqq"
                logger.info(f"[{self.name}] Estado restaurado: long {self.SYMBOL_LONG}")
            elif qty_hedge > 0:
                self._position_type = "long_psq"
                logger.info(f"[{self.name}] Estado restaurado: long {self.SYMBOL_HEDGE} (hedge)")
            else:
                self._position_type = None
                logger.info(f"[{self.name}] Sin posición activa al arrancar.")
        except Exception as e:
            logger.warning(f"[{self.name}] Error restaurando estado: {e}")

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        # Solo procesamos barras de QQQ para el cálculo del Z-Score
        if bar.symbol != self.SYMBOL_LONG:
            return

        close = float(bar.close)
        self._prices_buffer.append(close)

        # Agregar cada N barras en una sola (reduce cálculos y ruido)
        if len(self._prices_buffer) < self.BAR_AGGREGATE:
            return

        # Precio agregado = promedio de las últimas N barras de 1-min
        agg_price = sum(self._prices_buffer) / len(self._prices_buffer)
        self._prices_buffer.clear()
        self._aggregated.append(agg_price)

        if len(self._aggregated) < self.LOOKBACK:
            return

        # Calcular Z-Score
        prices_arr = np.array(self._aggregated)
        mean = prices_arr.mean()
        std  = prices_arr.std()

        if std == 0:
            return

        z_score = (agg_price - mean) / std

        # Logging throttled (cada 5 min, no cada barra)
        now = time.time()
        if now - self._last_log_time >= self.LOG_INTERVAL:
            logger.info(
                f"[{self.name}] {self.SYMBOL_LONG} "
                f"Price={agg_price:.2f} Z={z_score:.2f} pos={self._position_type}"
            )
            self._last_log_time = now

        # ── SEÑALES DE ENTRADA ──

        if z_score > self.Z_ENTRY and self._position_type is None:
            # QQQ sobrevalorado → Compra PSQ (hedge inverso, sube cuando QQQ baja)
            logger.info(
                f"[{self.name}] 📉 Z={z_score:.2f} > {self.Z_ENTRY} → "
                f"QQQ sobrevalorado. Comprando {self.SYMBOL_HEDGE} (inverso)"
            )
            await self.order_manager.buy(self.SYMBOL_HEDGE, strategy_name=self.name)
            self._position_type = "long_psq"

        elif z_score < -self.Z_ENTRY and self._position_type is None:
            # QQQ infravalorado → Compra QQQ (largo directo)
            logger.info(
                f"[{self.name}] 📈 Z={z_score:.2f} < -{self.Z_ENTRY} → "
                f"QQQ infravalorado. Comprando {self.SYMBOL_LONG}"
            )
            await self.order_manager.buy(self.SYMBOL_LONG, strategy_name=self.name)
            self._position_type = "long_qqq"

        # ── SEÑAL DE CIERRE ──

        elif abs(z_score) < self.Z_EXIT and self._position_type is not None:
            target = self.SYMBOL_LONG if self._position_type == "long_qqq" else self.SYMBOL_HEDGE
            logger.info(
                f"[{self.name}] ↔️ Spread normalizado (Z={z_score:.2f}). "
                f"Cerrando posición en {target}."
            )
            # Verificar que realmente existe la posición antes de vender
            qty = self.sync_position_from_alpaca(target)
            if qty > 0:
                await self.order_manager.sell(target, strategy_name=self.name)
            self._position_type = None
