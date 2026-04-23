import logging
import pandas as pd
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoPairDivergenceStrategy(BaseStrategy):
    """
    07 - Spot Pair Statistical Divergence (ETH/BTC)
    Timeframe 15m. Mide el ratio ETH/BTC. 
    Si ETH se vuelve demasiado 'barato' en términos de BTC (SMA 50 - 2 STD), compra ETH.
    """
    STRAT_NUMBER = 7
    SMA_PERIOD = 50

    def __init__(self, order_manager, regime_manager=None):
        super().__init__("Pair Divergence", ["BTC/USD", "ETH/USD"], order_manager)
        self.regime_manager = regime_manager
        self.last_btc_close = None
        self.last_eth_close = None
        self.ratios = deque(maxlen=self.SMA_PERIOD * 2)
        
        self.in_position = False
        self.current_qty = 0.0
        self.last_minute = -1

    async def on_bar(self, bar):
        if not self.should_process(bar.symbol):
            return

        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"):
            return

        # Timeframe control de 15m
        minute = bar.timestamp.minute
        is_15m_crossover = minute % 15 == 0 and minute != self.last_minute

        if bar.symbol == "BTC/USD":
            self.last_btc_close = bar.close
        elif bar.symbol == "ETH/USD":
            self.last_eth_close = bar.close

        if not self.last_btc_close or not self.last_eth_close:
            return

        if is_15m_crossover:
            self.last_minute = minute
            
            # Ratio: Precio ETH / Precio BTC
            ratio = self.last_eth_close / self.last_btc_close
            self.ratios.append(ratio)

            if len(self.ratios) < self.SMA_PERIOD:
                return

            s_ratios = pd.Series(list(self.ratios))
            sma = s_ratios.rolling(window=self.SMA_PERIOD).mean().iloc[-1]
            std = s_ratios.rolling(window=self.SMA_PERIOD).std().iloc[-1]

            lower_band = sma - (2 * std)

            if self.in_position:
                # Regresión a la media
                if ratio >= sma:
                    logger.info(f"[{self.name}] Ratio volvió a la media ({ratio:.5f}). Saliendo de ETH.")
                    await self.order_manager.sell_exact(
                        symbol="ETH/USD",
                        exact_qty=self.current_qty,
                        strategy_name=self.name
                    )
                    # Liberar ETH en el árbitro
                    self.order_manager.release_asset("ETH/USD", self.name)
                    self.in_position = False
                    self.current_qty = 0.0
            else:
                if ratio < lower_band:
                    # Consultar árbitro (P5 = mean reversion 15m)
                    granted = await self.order_manager.request_buy(
                        symbol="ETH/USD", priority=5, strategy_name=self.name
                    )
                    if not granted:
                        logger.debug(f"[{self.name}] Árbitro denegó compra ETH/USD. Divergencia omitida.")
                        return

                    logger.info(f"[{self.name}] Divergencia detectada. Ratio ({ratio:.5f}) < Band ({lower_band:.5f}). Comprando ETH.")
                    self.in_position = True
                    self.current_qty = round(100.0 / self.last_eth_close, 5)
                    await self.order_manager.buy(
                        symbol="ETH/USD",
                        notional_usd=100.0,
                        current_price=self.last_eth_close,
                        strategy_name=self.name
                    )
