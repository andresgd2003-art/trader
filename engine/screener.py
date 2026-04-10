"""
engine/screener.py
==================
PreMarketScreener — Dynamic Daily Universe para el Equities Engine.

Usa el Alpaca Screener API para obtener los top movers del mercado
cada mañana a las 09:00 AM EST, antes de que abra el mercado.

Documentación oficial:
  ScreenerClient.get_market_movers(MarketMoversRequest(top=20))
  - gainers: stocks que más subieron vs cierre anterior
  - losers: stocks que más bajaron vs cierre anterior

Filtros aplicados:
  - Precio: $1.00 a $25.00 (evitar stocks muy caros o penny stocks basura)
  - Top N configurable (default 15)
"""

import os
import logging
from typing import List, Tuple
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import MarketMoversRequest
from alpaca.data.enums import MarketType

logger = logging.getLogger(__name__)

# Columna de estado global para el dashboard
_DAILY_UNIVERSE: dict = {
    "gainers": [],
    "losers": [],
    "all": [],
    "last_updated": None,
}


def get_daily_universe() -> dict:
    """Retorna el universo del día (accesible desde el dashboard)."""
    return _DAILY_UNIVERSE


class PreMarketScreener:
    """
    Screener diario que se ejecuta antes de la apertura del mercado.
    Obtiene los top movers del día y construye el universo dinámico.
    """

    MIN_PRICE = 1.0
    MAX_PRICE = 25.0
    TOP_N = 20  # Pedir los top 20, filtrar a los que pasan el precio

    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.client = ScreenerClient(
            api_key=self.api_key,
            secret_key=self.secret_key
        )
        self.gainers: List[str] = []
        self.losers: List[str] = []

    def run(self) -> Tuple[List[str], List[str]]:
        """
        Ejecuta el screener y retorna (gainers, losers) del día.
        Aplica filtro de precio $1-$25.
        """
        global _DAILY_UNIVERSE
        from datetime import datetime

        try:
            logger.info("[Screener] Ejecutando PreMarket scan de top movers...")

            request = MarketMoversRequest(
                market_type=MarketType.STOCKS,
                top=self.TOP_N
            )
            movers = self.client.get_market_movers(request)

            # Filtrar gainers por rango de precio
            gainers = [
                m.symbol for m in movers.gainers
                if self.MIN_PRICE <= m.price <= self.MAX_PRICE
            ]

            # Filtrar losers por rango de precio
            losers = [
                m.symbol for m in movers.losers
                if self.MIN_PRICE <= m.price <= self.MAX_PRICE
            ]

            self.gainers = gainers[:10]  # Top 10 gainers filtrados
            self.losers = losers[:10]    # Top 10 losers filtrados
            all_tickers = list(set(self.gainers + self.losers))

            # Actualizar estado global para dashboard
            _DAILY_UNIVERSE["gainers"] = self.gainers
            _DAILY_UNIVERSE["losers"] = self.losers
            _DAILY_UNIVERSE["all"] = all_tickers
            _DAILY_UNIVERSE["last_updated"] = datetime.now().isoformat()

            logger.info(
                f"[Screener] ✅ Universo del día: {len(all_tickers)} tickers | "
                f"Gainers: {self.gainers} | Losers: {self.losers}"
            )

            return self.gainers, self.losers

        except Exception as e:
            logger.error(f"[Screener] ❌ Error al obtener top movers: {e}")
            logger.warning("[Screener] Usando universo de fallback vacío.")
            return [], []

    def get_all_symbols(self) -> List[str]:
        """Retorna todos los símbolos del universo actual (gainers + losers)."""
        return list(set(self.gainers + self.losers))
