"""
engine/news_risk_filter.py
===========================
Propuesta B — market-news-analyst Skill

Filtro de riesgo fundamental pre-entrada basado en noticias de Alpaca.
Solo se activa cuando el DailyModeManager tiene el modo B activo.

FLUJO:
  1. Bot genera señal BUY
  2. order_manager llama a NewsRiskFilter.get_risk(symbol)
  3. El filtro consulta NewsClient de Alpaca (sin API key requerida)
  4. Analiza titulares de las últimas 2-4 horas
  5. Clasifica riesgo: LOW / MEDIUM / HIGH
  6. order_manager decide ejecutar, reducir qty, o bloquear

CACHE:
  - 30 minutos por símbolo para no saturar la API
  - Máximo 50 entradas en cache (LRU implícito por dict insertion order)

KEYWORDS DE RIESGO:
  El skill market-news-analyst clasifica noticias en:
    HIGH:   bankruptcy, delisting, fraud, SEC investigation, FDA rejection,
            earnings miss, recall, guidance cut, class action, restatement
    MEDIUM: downgrade, miss, concern, warning, suspended, regulatory
    LOW:    todo lo demás (noticias neutras o positivas)
"""
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"
    UNKNOWN = "UNKNOWN"  # Error al consultar, pasar con cautela


# ── Keywords de clasificación (inspirado en market-news-analyst skill) ──

_HIGH_RISK_KEYWORDS = [
    "bankruptcy", "bankrupt", "delisting", "delisted", "fraud", "investigation",
    "sec charges", "fda reject", "fda denied", "clinical fail", "earnings miss",
    "missed estimates", "recall", "guidance cut", "guidance lowered",
    "class action", "restatement", "accounting irregularity", "going concern",
    "chapter 11", "default", "liquidation", "halted", "suspended trading",
    "implosion", "collapse", "crash", "massive loss",
]

_MEDIUM_RISK_KEYWORDS = [
    "downgrade", "downgraded", "miss", "disappoint", "concern", "warning",
    "headwinds", "regulatory", "probe", "subpoena", "inquiry", "layoffs",
    "restructuring", "write-off", "write-down", "impairment", "guidance",
    "lowered forecast", "cut target", "tariff", "sanction",
]


class NewsRiskFilter:
    """
    Filtro de noticias pre-entrada basado en el skill market-news-analyst.
    
    Uso:
        filter = NewsRiskFilter()
        risk = await filter.get_risk("AAPL")
        if risk == RiskLevel.HIGH:
            # no operar
    """

    CACHE_TTL_MINUTES = 30
    MAX_CACHE_ENTRIES = 50
    LOOKBACK_HOURS    = 4    # Noticias de las últimas 4 horas

    def __init__(self):
        self._cache: dict[str, dict] = {}  # {symbol: {risk, ts, headline_count}}

    def _is_cached(self, symbol: str) -> bool:
        if symbol not in self._cache:
            return False
        cached_time = self._cache[symbol]["ts"]
        age_minutes = (datetime.now(timezone.utc) - cached_time).total_seconds() / 60
        return age_minutes < self.CACHE_TTL_MINUTES

    def _classify_headline(self, headline: str) -> RiskLevel:
        """Clasifica un titular en LOW/MEDIUM/HIGH según keywords del skill."""
        h = headline.lower()
        for kw in _HIGH_RISK_KEYWORDS:
            if kw in h:
                return RiskLevel.HIGH
        for kw in _MEDIUM_RISK_KEYWORDS:
            if kw in h:
                return RiskLevel.MEDIUM
        return RiskLevel.LOW

    async def get_risk(self, symbol: str) -> RiskLevel:
        """
        Consulta las noticias recientes del símbolo y retorna el nivel de riesgo.
        Usa cache de 30 min para no saturar la API.
        
        Args:
            symbol: Símbolo bursátil (ej: "AAPL", "SPY")
        
        Returns:
            RiskLevel.LOW / MEDIUM / HIGH / UNKNOWN
        """
        # Normalizar símbolo (cripto usa "/", acciones no)
        clean_symbol = symbol.replace("/", "")

        if self._is_cached(clean_symbol):
            cached = self._cache[clean_symbol]
            logger.debug(f"[NewsFilter] {clean_symbol}: Risk={cached['risk']} (cached, {cached['count']} noticias)")
            return cached["risk"]

        try:
            # Importar NewsClient de alpaca-py (no requiere API key)
            from alpaca.data.historical.news import NewsClient
            from alpaca.data.requests import NewsRequest

            client = NewsClient()
            start_time = datetime.now(timezone.utc) - timedelta(hours=self.LOOKBACK_HOURS)

            request = NewsRequest(
                symbols=clean_symbol,
                start=start_time,
                limit=10,
                include_content=False  # Solo headline para velocidad
            )

            # Ejecutar en thread para no bloquear el event loop
            loop = asyncio.get_event_loop()
            news = await loop.run_in_executor(None, client.get_news, request)

            articles = news.news if hasattr(news, "news") else []
            if not articles:
                result = RiskLevel.LOW
                logger.debug(f"[NewsFilter] {clean_symbol}: Sin noticias recientes → LOW")
            else:
                # Tomar el peor nivel entre todos los titulares
                levels = [self._classify_headline(a.headline) for a in articles]
                if RiskLevel.HIGH in levels:
                    result = RiskLevel.HIGH
                elif RiskLevel.MEDIUM in levels:
                    result = RiskLevel.MEDIUM
                else:
                    result = RiskLevel.LOW

                worst_headline = next((a.headline for a in articles
                                      if self._classify_headline(a.headline) == result), "")
                logger.info(
                    f"[NewsFilter] {clean_symbol}: Risk={result.value} | "
                    f"{len(articles)} noticias | Peor: '{worst_headline[:80]}'"
                )

            # Guardar en cache (mantener máximo MAX_CACHE_ENTRIES)
            if len(self._cache) >= self.MAX_CACHE_ENTRIES:
                oldest = next(iter(self._cache))
                del self._cache[oldest]

            self._cache[clean_symbol] = {
                "risk":  result,
                "ts":    datetime.now(timezone.utc),
                "count": len(articles),
            }
            return result

        except Exception as e:
            logger.warning(f"[NewsFilter] Error consultando noticias de {clean_symbol}: {e}")
            return RiskLevel.UNKNOWN   # En caso de error, no bloquear

    def get_cache_status(self) -> list:
        """Retorna el estado del cache para el endpoint /api/news-filter."""
        now = datetime.now(timezone.utc)
        result = []
        for sym, data in self._cache.items():
            age = (now - data["ts"]).total_seconds() / 60
            result.append({
                "symbol":  sym,
                "risk":    data["risk"],
                "age_min": round(age, 1),
                "count":   data["count"],
                "valid":   age < self.CACHE_TTL_MINUTES,
            })
        return result


# Instancia global compartida entre order_managers
_NEWS_FILTER_INSTANCE: Optional[NewsRiskFilter] = None


def get_news_filter() -> NewsRiskFilter:
    """Retorna la instancia singleton del NewsRiskFilter."""
    global _NEWS_FILTER_INSTANCE
    if _NEWS_FILTER_INSTANCE is None:
        _NEWS_FILTER_INSTANCE = NewsRiskFilter()
    return _NEWS_FILTER_INSTANCE
