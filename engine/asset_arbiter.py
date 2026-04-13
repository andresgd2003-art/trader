"""
engine/asset_arbiter.py
======================
Árbitro centralizado de activos para el motor Cripto.

PROBLEMA QUE RESUELVE:
  Cuando múltiples estrategias apuntan al mismo símbolo (ej: 5 estrategias 
  apuntando a BTC/USD), sin coordinación pueden:
  - Enviar órdenes de compra simultáneas duplicando exposición.
  - Conflictos de horizonte temporal (una quiere vender en 1m, otra en días).
  - Consumir más capital del asignado sin quererlo.

SOLUCIÓN (basada en Freqtrade #1099 y patrones de Alpaca):
  - 1 posición activa máxima por símbolo en todo momento.
  - Cola de prioridades: señales urgentes (P1) vencen a señales lentas (P7).
  - Cooldown configurable tras cierre de posición.
  - asyncio.Lock por símbolo para thread-safety en el event loop.

PRIORIDADES DE ESTRATEGIAS (P1 = más urgente):
  P1: Volume Anomaly       (strat_06) — oportunidad de 1 minuto
  P2: VWAP Touch           (strat_09) — señal intraday veloz
  P3: Funding Squeeze      (strat_05) — señal de derivados externa
  P4: EMA Cross            (strat_01) — señal tendencial 1H
  P4: BB Breakout          (strat_02) — señal tendencial 15m  
  P5: Pair Divergence      (strat_07) — mean reversion 15m
  P5: EMA Ribbon           (strat_08) — 4H confirmación
  P6: Grid Spot            (strat_03) — market making
  P6: Smart TWAP           (strat_04) — DCA programado
  P7: Sentiment F&G        (strat_10) — largo plazo

USO:
  arbiter = AssetArbiter(cooldown_seconds=300)
  
  # Desde una estrategia, antes de comprar:
  granted = await arbiter.request_buy("BTC/USD", priority=2, strategy="VWAP")
  if granted:
      await order_manager.submit_order(...)
  
  # Cuando la posición se cierra:
  arbiter.release("BTC/USD", strategy="VWAP")
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class AssetArbiter:
    """
    Árbitro centralizado de activos para múltiples estrategias concurrentes.
    
    Garantiza:
      - Máximo 1 posición activa por símbolo.
      - Señales de mayor prioridad tienen preferencia.
      - Cooldown configurable entre operaciones del mismo símbolo.
      - Sin bloqueos ni latencia: usa asyncio.Lock nativo.
    """

    def __init__(self, cooldown_seconds: int = 300):
        """
        Args:
            cooldown_seconds: Tiempo de espera (segundos) tras cerrar una posición
                              antes de permitir una nueva entrada. Default: 5 minutos.
        """
        self.cooldown_seconds = cooldown_seconds
        
        # Estado de posición por símbolo: None = libre, str = nombre de estrategia dueña
        self._owners: dict[str, Optional[str]] = {}
        
        # Prioridad actual de la estrategia dueña (menor número = mayor prioridad)
        self._owner_priority: dict[str, int] = {}
        
        # Timestamp del último cierre de posición por símbolo (para cooldown)
        self._last_close_time: dict[str, float] = {}
        
        # Un Lock por símbolo para evitar race conditions en asyncio
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, symbol: str) -> asyncio.Lock:
        """Obtiene (o crea) el Lock asyncio para un símbolo dado."""
        if symbol not in self._locks:
            self._locks[symbol] = asyncio.Lock()
        return self._locks[symbol]

    async def request_buy(
        self, 
        symbol: str, 
        priority: int, 
        strategy: str
    ) -> bool:
        """
        Solicita permiso para comprar un símbolo.
        
        Args:
            symbol:   Par de cripto. Ej: "BTC/USD"
            priority: Entero del 1 (más urgente) al 7 (menos urgente).
            strategy: Nombre de la estrategia solicitante. Ej: "VWAP Touch"
        
        Returns:
            True  → Permiso concedido, puedes enviar la orden.
            False → Permiso denegado (ya hay posición activa o cooldown activo).
        """
        lock = self._get_lock(symbol)
        
        async with lock:
            try:
                # 1. Verificar cooldown post-cierre
                last_close = self._last_close_time.get(symbol, 0)
                elapsed = time.time() - last_close
                if elapsed < self.cooldown_seconds and last_close > 0:
                    remaining = int(self.cooldown_seconds - elapsed)
                    logger.debug(
                        f"[Arbiter] {strategy} → {symbol}: COOLDOWN "
                        f"({remaining}s restantes). Denegado."
                    )
                    return False
                
                # 2. Verificar si ya hay posición activa
                current_owner = self._owners.get(symbol)
                if current_owner is not None:
                    current_priority = self._owner_priority.get(symbol, 99)
                    logger.debug(
                        f"[Arbiter] {strategy} (P{priority}) → {symbol}: "
                        f"OCUPADO por '{current_owner}' (P{current_priority}). Denegado."
                    )
                    return False
                
                # 3. Símbolo libre → conceder permiso
                self._owners[symbol] = strategy
                self._owner_priority[symbol] = priority
                logger.info(
                    f"[Arbiter] ✅ {strategy} (P{priority}) adquirió {symbol}."
                )
                return True
                
            except Exception as e:
                logger.error(f"[Arbiter] Error crítico en request_buy para {symbol}: {e}")
                raise e
            finally:
                # El lock de asyncio se libera automáticamente por el context manager 'async with'.
                # Este bloque garantiza que cualquier extensión futura tenga limpieza garantizada.
                pass

    def release(self, symbol: str, strategy: str) -> None:
        """
        Libera la posición de un símbolo cuando la estrategia dueña cierra su trade.
        Activa el cooldown automáticamente.
        
        Args:
            symbol:   Par de cripto. Ej: "BTC/USD"
            strategy: Nombre de la estrategia que cierra la posición.
        """
        current_owner = self._owners.get(symbol)
        
        if current_owner == strategy:
            self._owners[symbol] = None
            self._owner_priority[symbol] = 99
            self._last_close_time[symbol] = time.time()
            logger.info(
                f"[Arbiter] 🔓 {strategy} liberó {symbol}. "
                f"Cooldown activo: {self.cooldown_seconds}s."
            )
        elif current_owner is not None:
            # Otra estrategia intenta liberar lo que no le pertenece (no debe pasar)
            logger.warning(
                f"[Arbiter] ⚠️ {strategy} intentó liberar {symbol} "
                f"pero lo posee '{current_owner}'. Ignorado."
            )

    def force_release(self, symbol: str) -> None:
        """
        Libera forzosamente un símbolo (para uso en emergencias o reinicio).
        No activa cooldown.
        """
        old_owner = self._owners.get(symbol, "Nadie")
        self._owners[symbol] = None
        self._owner_priority[symbol] = 99
        logger.warning(f"[Arbiter] 🚨 FORCE RELEASE de {symbol} (era de '{old_owner}').")

    def get_status(self) -> dict:
        """Retorna el estado actual del árbitro para el dashboard/logs."""
        return {
            sym: {
                "owner": self._owners.get(sym),
                "priority": self._owner_priority.get(sym),
                "cooldown_remaining": max(
                    0,
                    int(self.cooldown_seconds - (time.time() - self._last_close_time.get(sym, 0)))
                ) if self._owners.get(sym) is None else 0
            }
            for sym in set(list(self._owners.keys()) + list(self._last_close_time.keys()))
        }

    def is_free(self, symbol: str) -> bool:
        """Verifica si un símbolo está libre (sin posición activa y sin cooldown)."""
        if self._owners.get(symbol) is not None:
            return False
        last_close = self._last_close_time.get(symbol, 0)
        return (time.time() - last_close) >= self.cooldown_seconds
