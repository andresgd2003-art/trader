"""
strategies/strat_09_pairs.py — Pairs Trading (QQQ / XLK)
=========================================================
LÓGICA:
- Calcula el spread entre dos activos correlacionados: QQQ y XLK
  Spread = Precio(QQQ) / Precio(XLK)
- Calcula el Z-Score del spread en los últimos 20 días
- Si Z-Score > 2: QQQ está muy caro vs XLK → Short QQQ, Long XLK
- Si Z-Score < -2: QQQ está muy barato vs XLK → Long QQQ, Short XLK

FIXES DE SEGURIDAD:
- Restaura _position_type desde Alpaca al reiniciar (evita shorts huérfanos)
- Verifica posición real antes de cerrar (evita errores 40410000)
- Solo opera si hay precio de ambos activos
"""
import logging
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class PairsTradingStrategy(BaseStrategy):

    SYMBOL_A = "QQQ"
    SYMBOL_B = "XLK"
    LOOKBACK  = 20
    Z_ENTRY   = 2.0
    Z_EXIT    = 0.5
    QTY       = 5       # Reducido de 10 a 5 para menor exposición

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Pairs Trading",
            symbols=[self.SYMBOL_A, self.SYMBOL_B],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._price_a = None
        self._price_b = None
        self._spreads = deque(maxlen=self.LOOKBACK)
        self._position_type = None  # "long_a_short_b" | "short_a_long_b"

        # Restaurar estado real desde Alpaca al reiniciar
        self._restore_state()

    def _restore_state(self):
        """Sincroniza _position_type con las posiciones reales en Alpaca al arrancar."""
        try:
            qty_a = self.sync_position_from_alpaca(self.SYMBOL_A)
            qty_b = self.sync_position_from_alpaca(self.SYMBOL_B)

            if qty_a > 0 and qty_b < 0:
                self._position_type = "long_a_short_b"
                logger.info(f"[{self.name}] Estado restaurado: long {self.SYMBOL_A} / short {self.SYMBOL_B}")
            elif qty_a < 0 and qty_b > 0:
                self._position_type = "short_a_long_b"
                logger.info(f"[{self.name}] Estado restaurado: short {self.SYMBOL_A} / long {self.SYMBOL_B}")
            else:
                self._position_type = None
                logger.info(f"[{self.name}] Sin posición activa al arrancar.")
        except Exception as e:
            logger.warning(f"[{self.name}] Error restaurando estado: {e}")

    def _has_real_position(self, symbol: str) -> bool:
        """Verifica que la posición realmente existe en Alpaca antes de intentar cerrarla."""
        qty = self.sync_position_from_alpaca(symbol)
        return qty != 0.0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        if bar.symbol == self.SYMBOL_A:
            self._price_a = float(bar.close)
        elif bar.symbol == self.SYMBOL_B:
            self._price_b = float(bar.close)

        if self._price_a is None or self._price_b is None:
            return

        spread = self._price_a / self._price_b
        self._spreads.append(spread)

        if len(self._spreads) < self.LOOKBACK:
            return

        spreads_arr = np.array(self._spreads)
        mean = spreads_arr.mean()
        std  = spreads_arr.std()

        if std == 0:
            return

        z_score = (spread - mean) / std

        logger.info(
            f"[{self.name}] {self.SYMBOL_A}/{self.SYMBOL_B} "
            f"Spread={spread:.4f} Z={z_score:.2f} pos={self._position_type}"
        )

        if z_score > self.Z_ENTRY and self._position_type is None:
            logger.info(f"[{self.name}] Z={z_score:.2f} > {self.Z_ENTRY} → Short {self.SYMBOL_A}, Long {self.SYMBOL_B}")
            await self.order_manager.sell(self.SYMBOL_A, strategy_name=self.name)
            await self.order_manager.buy(self.SYMBOL_B, strategy_name=self.name)
            self._position_type = "short_a_long_b"

        elif z_score < -self.Z_ENTRY and self._position_type is None:
            logger.info(f"[{self.name}] Z={z_score:.2f} < -{self.Z_ENTRY} → Long {self.SYMBOL_A}, Short {self.SYMBOL_B}")
            await self.order_manager.buy(self.SYMBOL_A, strategy_name=self.name)
            await self.order_manager.sell(self.SYMBOL_B, strategy_name=self.name)
            self._position_type = "long_a_short_b"

        elif abs(z_score) < self.Z_EXIT and self._position_type is not None:
            logger.info(f"[{self.name}] Spread normalizado (Z={z_score:.2f}). Cerrando posición.")
            if self._position_type == "short_a_long_b":
                if self._has_real_position(self.SYMBOL_A):
                    await self.order_manager.buy(self.SYMBOL_A, strategy_name=self.name)
                if self._has_real_position(self.SYMBOL_B):
                    await self.order_manager.sell(self.SYMBOL_B, strategy_name=self.name)
            else:
                if self._has_real_position(self.SYMBOL_A):
                    await self.order_manager.sell(self.SYMBOL_A, strategy_name=self.name)
                if self._has_real_position(self.SYMBOL_B):
                    await self.order_manager.buy(self.SYMBOL_B, strategy_name=self.name)
            self._position_type = None
            self._position = {}
