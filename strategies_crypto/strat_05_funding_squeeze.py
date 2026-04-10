import logging
import asyncio
import aiohttp
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoFundingSqueezeStrategy(BaseStrategy):
    """
    05 - Funding Rate Short-Squeeze Proxy
    Chequea en intervalos de 5 minutos la tasa de funding de futuros perpetuos Binance.
    Si la tasa es < -0.05%, los shorts están atrapados y pagan a los longs (squeeze probable).
    """

    def __init__(self, order_manager):
        super().__init__("Funding Squeeze", ["ETH/USD"], order_manager)
        self.entry_price = 0.0
        self.max_price = 0.0
        self.in_position = False
        self.last_check_minute = -1
        self.BINANCE_API = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=ETHUSDT"

    async def fetch_funding_rate(self) -> float:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.BINANCE_API, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # lastFundingRate es devuelto como string
                        return float(data.get("lastFundingRate", 0.0))
        except Exception as e:
            logger.warning(f"[{self.name}] Error fetching Binance API: {e}")
        return 0.0

    async def on_bar(self, bar):
        # Update trailing params
        if self.in_position:
            if bar.high > self.max_price:
                self.max_price = bar.high
            
            # Trailing stop del 2%
            trailing_stop = self.max_price * 0.98
            
            loss_pct = (bar.close - self.entry_price) / self.entry_price * 100
            
            # Salida (Trailing stop interactúa cada minuto)
            if bar.close <= trailing_stop:
                logger.info(f"[{self.name}] Saliendo por Trailing Stop de 2%.")
                await self._close_position(bar.symbol, bar.close)
                return

        # Solo hacer la petición externa cada 5 minutos exactos del reloj
        current_minute = bar.timestamp.minute
        if current_minute % 5 == 0 and current_minute != self.last_check_minute:
            self.last_check_minute = current_minute
            
            funding_rate = await self.fetch_funding_rate()
            # funding_rate = -0.0005 equial a -0.05%
            
            # Si estamos dentro, verificamos si la tasa ya se normalizó (> 0)
            if self.in_position:
                if funding_rate >= 0.0:
                    logger.info(f"[{self.name}] Funding normalizado ({funding_rate*100:.3f}%). Saliendo.")
                    await self._close_position(bar.symbol, bar.close)
            else:
                # Si no estamos dentro, evaluamos entry
                if funding_rate <= -0.0005:  # -0.05%
                    # Consultar árbitro (P3 = urgencia media)
                    granted = await self.order_manager.request_buy(
                        symbol=bar.symbol, priority=3, strategy_name=self.name
                    )
                    if not granted:
                        logger.debug(f"[{self.name}] Árbitro denegó compra ETH. Squeeze omitido.")
                        return

                    logger.info(f"[{self.name}] ALERTA Squeeze: Funding Rate en {funding_rate*100:.3f}%. COMPRANDO.")
                    self.in_position = True
                    self.entry_price = bar.close
                    self.max_price = bar.close
                    qty = round(100.0 / bar.close, 5)
                    await self.order_manager.buy(
                        symbol=bar.symbol,
                        notional_usd=100.0,
                        current_price=bar.close,
                        strategy_name=self.name
                    )

    async def _close_position(self, symbol, current_price):
        if self.in_position:
            qty = round(100.0 / current_price, 5)
            await self.order_manager.sell_exact(
                symbol=symbol,
                exact_qty=qty,
                strategy_name=self.name
            )
            # Liberar el símbolo en el árbitro
            self.order_manager.release_asset(symbol, self.name)
            self.in_position = False
            self.entry_price = 0.0
            self.max_price = 0.0
