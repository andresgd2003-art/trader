import logging
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoGridSpotStrategy(BaseStrategy):

    SYMBOL = "SOL/USD"
    TRANCHES = 5
    INTERVAL_PCT = 0.015  # 1.5%
    TOTAL_ALLOCATION_USD = 1000.0

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Dynamic Spot Grid",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._vwap_baseline = 0.0
        self._tranche_usd = self.TOTAL_ALLOCATION_USD / self.TRANCHES
        self._active_bids = {} # { order_id: price }
        self._active_asks = {} # { order_id: price }
        self._grid_deployed = False  # ⚠️ Guard anti-duplicados en reinicios
        
        self._cumulative_vol = 0.0
        self._cumulative_pv = 0.0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(3, engine='crypto'):
            return

        # Simple VWAP tracking since boot
        typ_price = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        vol = float(bar.volume)
        self._cumulative_vol += vol
        self._cumulative_pv += typ_price * vol
        
        current_vwap = self._cumulative_pv / self._cumulative_vol if self._cumulative_vol > 0 else typ_price

        # Si aún no tenemos un VWAP solido de +10 velas, usamos el close y guardamos baseline
        if self._vwap_baseline == 0.0:
            self._vwap_baseline = current_vwap
            # ⚠️ GUARD: Solo desplegamos si no hay grid activa (anti-duplicados en reinicios)
            if not self._grid_deployed:
                self._grid_deployed = True
                await self._deploy_grid(self._vwap_baseline, float(bar.close))
        else:
            # Rebalanceo si el mercado se mueve +- 5% respecto al vwap original anclado
            drift = abs(current_vwap - self._vwap_baseline) / self._vwap_baseline
            if drift > 0.05:
                logger.info(f"[{self.name}] 🔄 VWAP Drift > 5%. Cancelando y Redesplegando Grid.")
                self._vwap_baseline = current_vwap
                # En un entorno real se mandarían cancelaciones al API via client.cancel_all_orders()
                # Para simplificar arquitectura, limpiamos estado local.
                self._active_bids.clear()
                self._grid_deployed = False  # Permitir redespliegue tras drift
                await self._deploy_grid(self._vwap_baseline, float(bar.close))

    async def _deploy_grid(self, baseline_price: float, current_price: float):
        logger.info(f"[{self.name}] 🏗️ Construyendo Grid bajo Base ${baseline_price:.2f}")
        for i in range(1, self.TRANCHES + 1):
            target_price = baseline_price * (1.0 - (self.INTERVAL_PCT * i))
            
            # Solo podemos mandar Limit orders. 
            await self.order_manager.buy(
                symbol=self.SYMBOL,
                notional_usd=self._tranche_usd,
                current_price=current_price,
                limit_price=round(target_price, 2),
                strategy_name=self.name
            )

    # Nota: El Grid exige reacción a Orders Filled inmediatos.
    # El websocket de updates permitiría capturarlo y emitir el ASK.
    # Esto es una base simplificada.
