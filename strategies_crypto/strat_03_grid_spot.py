"""
strat_03_grid_spot.py — Dynamic Spot Grid SOL/USD (Cash Account Compatible)
============================================================================
LÓGICA COMPLETA (BUY + SELL):
  Grid activa de mean-reversion sobre SOL/USD.
  
  - Calcula un VWAP rolling como precio "justo"
  - COMPRA cuando SOL cae un % debajo del VWAP (dip buying)
  - VENDE cuando SOL sube un % desde el precio de entrada (take profit)
  - Máximo 3 posiciones escalonadas simultáneas (tranches)
  - Respeta el cap del OrderManagerCrypto ($15 día / $40 noche)
  
CPU OPTIMIZATION:
  - Solo evalúa cada 5 barras (5 minutos)
  - Logging throttled
"""
import logging
import time
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class CryptoGridSpotStrategy(BaseStrategy):

    SYMBOL = "SOL/USD"
    
    # Grid Parameters
    DIP_ENTRY_PCT    = 0.015   # Compra cuando el precio baja 1.5% del VWAP
    TAKE_PROFIT_PCT  = 0.025   # Vende cuando sube 2.5% desde la entrada
    STOP_LOSS_PCT    = 0.05    # Stop loss si baja 5% desde entrada
    MAX_TRANCHES     = 3       # Máximo 3 posiciones escalonadas
    TRANCHE_SPACING  = 0.015   # Cada tranche adicional requiere -1.5% más de dip
    VWAP_WINDOW      = 60      # Ventana VWAP (60 barras = 1 hora)
    EVAL_EVERY_N     = 5       # Evaluar cada N barras (5 min)
    LOG_INTERVAL     = 300     # Loguear cada 5 minutos
    MAX_HOLD_SECS    = 86400   # 24h — forzar venta si tranche no cierra en este tiempo

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Dynamic Spot Grid",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        
        # VWAP tracking
        self._cumulative_vol = 0.0
        self._cumulative_pv = 0.0
        self._prices = deque(maxlen=self.VWAP_WINDOW)
        self._volumes = deque(maxlen=self.VWAP_WINDOW)
        
        # Grid state
        self._tranches: list[dict] = []  # [{entry_price, qty, timestamp}]
        self._bar_count = 0
        self._last_log_time = 0
        
        # Restore existing position at startup
        self._restore_state()

    def _restore_state(self):
        """Sincroniza posiciones reales desde Alpaca al arrancar."""
        try:
            qty = self.sync_position_from_alpaca(self.SYMBOL)
            if qty > 0:
                # Tenemos SOL en cartera — registrar como 1 tranche genérica
                self._tranches = [{"entry_price": 0.0, "qty": qty, "timestamp": time.time()}]
                logger.info(f"[{self.name}] Estado restaurado: {qty} SOL en posición.")
            else:
                logger.info(f"[{self.name}] Sin posición SOL al arrancar. Grid lista.")
        except Exception as e:
            logger.warning(f"[{self.name}] Error restaurando estado: {e}")

    def _calc_vwap(self) -> float:
        """Calcula VWAP rolling sobre la ventana."""
        if not self._prices or self._cumulative_vol <= 0:
            return 0.0
        return self._cumulative_pv / self._cumulative_vol

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        close = float(bar.close)
        vol = float(bar.volume)
        
        # Acumular VWAP
        typ_price = (float(bar.high) + float(bar.low) + close) / 3.0
        self._prices.append(close)
        self._volumes.append(vol)
        self._cumulative_vol += vol
        self._cumulative_pv += typ_price * vol
        
        # Decay VWAP para que sea rolling (no infinito)
        if len(self._prices) >= self.VWAP_WINDOW:
            oldest_vol = self._volumes[0] if self._volumes else 0
            oldest_pv = (self._prices[0] if self._prices else 0) * oldest_vol
            self._cumulative_vol = max(self._cumulative_vol - oldest_vol, 0.001)
            self._cumulative_pv = max(self._cumulative_pv - oldest_pv, 0.0)

        # Solo evaluar cada N barras para ahorrar CPU
        self._bar_count += 1
        if self._bar_count % self.EVAL_EVERY_N != 0:
            return

        vwap = self._calc_vwap()
        if vwap <= 0:
            return

        # Logging throttled
        now = time.time()
        if now - self._last_log_time >= self.LOG_INTERVAL:
            logger.info(
                f"[{self.name}] SOL ${close:.2f} | VWAP ${vwap:.2f} | "
                f"Tranches: {len(self._tranches)}/{self.MAX_TRANCHES}"
            )
            self._last_log_time = now

        # ── LÓGICA DE VENTA (Take Profit / Stop Loss / Timeout) ──
        tranches_to_close = []
        for i, tranche in enumerate(self._tranches):
            entry = tranche["entry_price"]
            if entry <= 0:
                # Tranche restaurada sin precio de entrada — usar PRECIO ACTUAL (no VWAP)
                entry = close
                tranche["entry_price"] = entry
                logger.info(f"[{self.name}] Tranche #{i+1} entry_price restaurado a ${entry:.2f}")
            
            pct_change = (close - entry) / entry
            
            # Timeout: forzar venta si la tranche tiene más de 24h
            age_secs = time.time() - tranche.get("timestamp", time.time())
            if age_secs >= self.MAX_HOLD_SECS:
                logger.info(
                    f"[{self.name}] ⏰ TIMEOUT tranche #{i+1}: "
                    f"{age_secs/3600:.1f}h > 24h. Forzando venta."
                )
                tranches_to_close.append(i)
            elif pct_change >= self.TAKE_PROFIT_PCT:
                logger.info(
                    f"[{self.name}] 💰 TAKE PROFIT tranche #{i+1}: "
                    f"SOL ${close:.2f} (+{pct_change*100:.1f}% desde ${entry:.2f})"
                )
                tranches_to_close.append(i)
            elif pct_change <= -self.STOP_LOSS_PCT:
                logger.info(
                    f"[{self.name}] 🛑 STOP LOSS tranche #{i+1}: "
                    f"SOL ${close:.2f} ({pct_change*100:.1f}% desde ${entry:.2f})"
                )
                tranches_to_close.append(i)

        # Ejecutar ventas (en orden inverso para no romper índices)
        for i in reversed(tranches_to_close):
            tranche = self._tranches[i]
            try:
                real_qty = self.sync_position_from_alpaca(self.SYMBOL)
                if real_qty > 0:
                    sell_qty = min(tranche["qty"], real_qty)
                    if sell_qty > 0:
                        await self.order_manager.sell_exact(
                            symbol=self.SYMBOL,
                            exact_qty=sell_qty,
                            strategy_name=self.name
                        )
                        self.order_manager.release_asset(self.SYMBOL, self.name)
            except Exception as e:
                logger.error(f"[{self.name}] Error vendiendo tranche #{i+1}: {e}")
            self._tranches.pop(i)

        # ── LÓGICA DE COMPRA (Dip Buying escalonado) ──
        if len(self._tranches) >= self.MAX_TRANCHES:
            return  # Grid llena

        # Calcular el dip requerido para el siguiente tranche
        next_tranche_num = len(self._tranches)
        required_dip = self.DIP_ENTRY_PCT + (self.TRANCHE_SPACING * next_tranche_num)
        
        dip_from_vwap = (vwap - close) / vwap  # Positivo si el precio está DEBAJO del VWAP

        if dip_from_vwap >= required_dip:
            # Verificar con el árbitro
            can_buy = await self.order_manager.request_buy(
                symbol=self.SYMBOL,
                priority=4,
                strategy_name=self.name
            )
            if not can_buy:
                return

            logger.info(
                f"[{self.name}] 📊 DIP BUY tranche #{next_tranche_num+1}: "
                f"SOL ${close:.2f} ({dip_from_vwap*100:.1f}% debajo de VWAP ${vwap:.2f})"
            )
            
            # El OrderManagerCrypto aplica su cap automáticamente ($15/$40)
            await self.order_manager.buy(
                symbol=self.SYMBOL,
                notional_usd=100.0,  # Aspiracional — será capeado a $15/$40
                current_price=close,
                strategy_name=self.name
            )
            
            # Registrar tranche con precio real de entrada
            cap = self.order_manager._get_dynamic_cap()
            real_qty = round(cap / close, 6)
            self._tranches.append({
                "entry_price": close,
                "qty": real_qty,
                "timestamp": time.time()
            })
