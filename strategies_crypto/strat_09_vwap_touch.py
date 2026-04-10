import logging
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoVWAPTouchStrategy(BaseStrategy):
    """
    09 - Intraday VWAP Touch-and-Go
    Asset: BTC/USD
    Timeframe: 1m
    Calcula el VWAP diario reseteado a las 00:00 UTC.
    Entra si el precio ha estado arriba del VWAP por 60 min y de repente retrocede a tocarlo.
    """
    def __init__(self, order_manager):
        super().__init__("VWAP Touch-and-Go", ["BTC/USD"], order_manager)
        
        self.vwap_sum_pv = 0.0
        self.vwap_sum_v = 0.0
        self.current_vwap = 0.0
        self.last_day = -1
        
        self.minutes_above_vwap = 0
        
        self.in_position = False
        self.entry_vwap = 0.0
        self.current_qty = 0.0

    async def on_bar(self, bar):
        dt = bar.timestamp
        # Reset VWAP a la medianoche UTC
        if dt.day != self.last_day:
            self.last_day = dt.day
            self.vwap_sum_pv = 0.0
            self.vwap_sum_v = 0.0
            self.minutes_above_vwap = 0
            
        typical_price = (bar.high + bar.low + bar.close) / 3
        self.vwap_sum_pv += typical_price * bar.volume
        self.vwap_sum_v += bar.volume
        
        if self.vwap_sum_v > 0:
            self.current_vwap = self.vwap_sum_pv / self.vwap_sum_v

        if self.current_vwap == 0:
            return

        if self.in_position:
            # Hard stop: 1% debajo del VWAP con el que entró
            stop_loss = self.entry_vwap * 0.99
            # Take profit: 2% arriba del VWAP con el que entró
            take_profit = self.entry_vwap * 1.02
            
            if bar.close <= stop_loss or bar.close >= take_profit:
                reason = "Stop Loss de 1%" if bar.close <= stop_loss else "Take Profit de 2%"
                logger.info(f"[{self.name}] Saliendo por {reason}. Precio: {bar.close}")
                await self.order_manager.submit_order(
                    symbol=bar.symbol, qty=self.current_qty, side="sell", type="market", strategy_id=f"cry_vwapsell"
                )
                self.in_position = False
                self.current_qty = 0.0
                self.entry_vwap = 0.0
                return

        # Rastrear si está arriba del vwap consistentemente
        if bar.close > self.current_vwap:
            self.minutes_above_vwap += 1
        else:
            # Si tocó o bajó y venía de estar 60 mins arriba, evaluamos comprar
            if not self.in_position and self.minutes_above_vwap >= 60:
                if bar.close <= self.current_vwap * 1.001:
                    logger.info(f"[{self.name}] VWAP Bounce detectado! (1h arriba y pullback a {self.current_vwap:.2f}). Comprando!")
                    self.in_position = True
                    self.entry_vwap = self.current_vwap
                    self.current_qty = round(100.0 / bar.close, 5)
                    await self.order_manager.submit_order(
                        symbol=bar.symbol, qty=self.current_qty, side="buy", type="market", strategy_id=f"cry_vwapbuy"
                    )
            
            # Reset contador si rompió el vwap
            self.minutes_above_vwap = 0
