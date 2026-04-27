import logging
import numpy as np
import pandas as pd
from collections import deque
from ta.trend import SMAIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoVolAnomalyStrategy(BaseStrategy):
    """
    06 - Volume Anomaly / Pump Catcher
    Busca picos anormales de volumen en 1 minuto en High Beta Altcoins (LINK/USD).
    """
    STRAT_NUMBER = 6
    SMA_VOL_PERIOD = 20
    FORCED_STOP_LOSS_PCT   = 0.04   # -4%
    FORCED_TAKE_PROFIT_PCT = 0.06   # +6%

    def __init__(self, order_manager, regime_manager=None):
        super().__init__("Volume Anomaly", ["LINK/USD"], order_manager)
        self.regime_manager = regime_manager
        self._volumes = deque(maxlen=self.SMA_VOL_PERIOD * 2)
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        qty = self.sync_position_from_alpaca("LINK/USD")
        self.in_position = qty > 0
        self.entry_price = 0.0
        self.max_price = 0.0
        self.current_qty = qty
        # Recuperar avg_entry_price real desde Alpaca para no perderlo al restart
        if self.in_position:
            try:
                pos = self.order_manager.broker.get_position("LINK/USD") if hasattr(self.order_manager, "broker") else None
                if pos:
                    self.entry_price = float(pos.avg_entry_price)
                    self.max_price = self.entry_price
            except Exception:
                pass

    async def on_bar(self, bar):
        if not self.should_process(bar.symbol):
            return


        self._volumes.append(bar.volume)

        # === FORCED EXIT (SL -4% / TP +6%) por entry_price tracking ===
        if self.in_position and self.entry_price > 0 and self.current_qty > 0:
            pnl_pct = (float(bar.close) - self.entry_price) / self.entry_price
            if pnl_pct <= -self.FORCED_STOP_LOSS_PCT or pnl_pct >= self.FORCED_TAKE_PROFIT_PCT:
                tag = "SL -4%" if pnl_pct <= -self.FORCED_STOP_LOSS_PCT else "TP +6%"
                logger.warning(f"[{self.name}] FORCED {tag} LINK. entry={self.entry_price:.4f} now={float(bar.close):.4f}")
                real_qty = self.sync_position_from_alpaca("LINK/USD")
                exact_qty = min(real_qty, self.current_qty) if real_qty > 0 else 0
                if exact_qty > 0:
                    await self.order_manager.sell_exact(
                        symbol=bar.symbol, exact_qty=exact_qty, strategy_name=self.name
                    )
                self.order_manager.release_asset(bar.symbol, self.name)
                self.in_position = False
                self.entry_price = 0.0
                self.max_price = 0.0
                self.current_qty = 0.0
                return

        # Manejo del trailing stop primero
        if self.in_position:
            if bar.high > self.max_price:
                self.max_price = bar.high
            
            ts_price = self.max_price * 0.985 # 1.5%
            if bar.close <= ts_price:
                logger.info(f"[{self.name}] Saliendo por TSL de 1.5%. Max: {self.max_price}")
                await self.order_manager.sell_exact(
                    symbol=bar.symbol,
                    exact_qty=self.current_qty,
                    strategy_name=self.name
                )
                # Liberar el símbolo en el árbitro
                self.order_manager.release_asset(bar.symbol, self.name)
                self.in_position = False
                self.entry_price = 0.0
                self.max_price = 0.0
                self.current_qty = 0.0
                return

        if len(self._volumes) < self.SMA_VOL_PERIOD:
            return

        if not self.in_position:
            vol_s = pd.Series(list(self._volumes)[:-1]) # sma sin incluir barra actual para no sesgar
            if len(vol_s) < self.SMA_VOL_PERIOD:
                return
            sma_vol = SMAIndicator(vol_s, window=self.SMA_VOL_PERIOD).sma_indicator().iloc[-1]
            
            if sma_vol > 0:
                is_pump = bar.volume > (sma_vol * 5)
                is_bullish = bar.close > bar.open
                
                if is_pump and is_bullish:
                    # Consultar árbitro (P1 = máxima urgencia, pump de 1 minuto)
                    if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
                    granted = await self.order_manager.request_buy(
                        symbol=bar.symbol, priority=1, strategy_name=self.name
                    )
                    if not granted:
                        logger.debug(f"[{self.name}] Árbitro denegó compra LINK. Pump perdido.")
                        return

                    logger.info(f"[{self.name}] ANOMALIA DETECTADA. Vol: {bar.volume} | SMA: {sma_vol}. Comprando!")
                    self.in_position = True
                    self.entry_price = bar.close
                    self.max_price = bar.close
                    self.current_qty = round(100.0 / bar.close, 5)
                    if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
                    await self.order_manager.buy(
                        symbol=bar.symbol,
                        notional_usd=100.0,
                        current_price=bar.close,
                        strategy_name=self.name
                    )
