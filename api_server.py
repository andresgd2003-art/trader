"""
api_server.py — AlpacaNode Dashboard API
==========================================
FastAPI server que expone datos al dashboard en tiempo real.
Corre en un thread separado dentro del proceso de main.py.

Endpoints:
  GET  /api/account     → Balance, equity, P&L
  GET  /api/positions   → Posiciones abiertas
  GET  /api/orders      → Últimas 30 órdenes
  GET  /api/history     → Historia del portafolio (para el gráfico)
  GET  /api/logs        → Últimas 100 líneas del log
  GET  /                → Dashboard HTML
"""
import os
import json
import logging
import csv
import io
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

logger = logging.getLogger("api_server")

# ============================================================
# CONFIGURACIÓN
# ============================================================
API_KEY    = os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
PAPER      = os.environ.get("PAPER_TRADING", "True").lower() == "true"
LOG_PATH   = os.environ.get("LOG_PATH", "/app/data/engine.log")

# ============================================================
# APP FASTAPI
# ============================================================
app = FastAPI(title="AlpacaNode Dashboard API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

def get_trading_client() -> TradingClient:
    return TradingClient(api_key=API_KEY, secret_key=SECRET_KEY, paper=PAPER)

# ============================================================
# MOTOR HISTÓRICO ASÍNCRONO (LATENCIA CERO)
# ============================================================
_CHART_CACHE = {
    "home": [],
    "etf": [],
    "crypto": [],
    "eq": []
}

async def _build_charts_task():
    """Background task para evitar latencia de cálculos. Compila The PNL Curve históricamente."""
    global _CHART_CACHE
    while True:
        try:
            client = get_trading_client()
            # Descargamos historial robusto (1000 operaciones) para recrear gráficas estables
            orders = client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1000)
            )
            
            valid = [o for o in orders if o.client_order_id and (
                str(o.client_order_id).startswith("strat_") or 
                str(o.client_order_id).startswith("cry_") or 
                str(o.client_order_id).startswith("eq_")
            )]
            valid.sort(key=lambda x: (x.filled_at if x.filled_at else x.created_at) if (x.filled_at or x.created_at) else datetime.min)
            
            tracker = {} 
            engines_pnl = {"home": 0.0, "etf": 0.0, "crypto": 0.0, "eq": 0.0}
            new_cache = {"home": [], "etf": [], "crypto": [], "eq": []}
            
            for o in valid:
                if o.status.value == "filled":
                    qty = float(o.filled_qty) if o.filled_qty else 0
                    price = float(o.filled_avg_price) if o.filled_avg_price else 0
                    vol = qty * price
                    
                    # Fase 17+: usar parse_order_meta para engine correcto
                    meta = parse_order_meta(o.client_order_id)
                    strat_name = meta["name"]
                    tracker_key = f"{strat_name}_{o.symbol}"
                    if tracker_key not in tracker:
                        tracker[tracker_key] = {"pos": 0.0, "avg": 0.0}
                    
                    pos = tracker[tracker_key]["pos"]
                    avg = tracker[tracker_key]["avg"]
                    
                    if o.side.value == "buy":
                        new_cost = (pos * avg) + vol
                        pos += qty
                        avg = new_cost / pos if pos > 0 else 0
                        tracker[tracker_key] = {"pos": pos, "avg": avg}
                    else:
                        realized = (price - avg) * qty
                        pos -= qty
                        if pos <= 0: pos = 0.0; avg = 0.0
                        tracker[tracker_key] = {"pos": pos, "avg": avg}
                        
                        meta = parse_order_meta(o.client_order_id)
                        engine_key = meta["engine"] if meta["engine"] in ("etf", "crypto", "equities") else "etf"
                        if engine_key == "equities": engine_key = "eq"  # alias para el cache
                        
                        engines_pnl[engine_key] += realized
                        engines_pnl["home"] += realized
                        
                        date_str = o.filled_at.strftime("%Y-%m-%d %H:%M")
                        
                        # Solo inyectar en la gráfica si existió cambio económico real
                        if realized != 0:
                            new_cache[engine_key].append({"date": date_str, "equity": engines_pnl[engine_key], "engine": engine_key})
                            new_cache["home"].append({"date": date_str, "equity": engines_pnl["home"], "engine": "home"})

            # ─── HOME: Portfolio history oficial de Alpaca ──────────────────────────────────
            # Usa GetPortfolioHistoryRequest para obtener la curva de equity REAL
            # (igual a la que muestra Alpaca en su web/app)
            try:
                ph = client.get_portfolio_history(
                    GetPortfolioHistoryRequest(
                        period="1M",        # 1 mes de datos
                        timeframe="1D",     # vela diaria (ligero)
                        extended_hours=False
                    )
                )
                home_points = []
                if ph and ph.timestamp and ph.equity:
                    for ts, eq in zip(ph.timestamp, ph.equity):
                        if eq and eq > 0:
                            from datetime import timezone as _tz
                            import datetime as _dt
                            date_str = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
                            home_points.append({"date": date_str, "equity": round(float(eq), 2), "engine": "home"})
                if home_points:
                    new_cache["home"] = home_points
                    logger.info(f"[Charts] Home: {len(home_points)} puntos de portfolio history de Alpaca")
            except Exception as ph_err:
                logger.warning(f"[Charts] No se pudo obtener portfolio history de Alpaca: {ph_err}. Usando cálculo local.")
                # Fallback: usar el home calculado localmente por órdenes
                if new_cache["home"]:
                    pass  # ya tiene datos del loop de órdenes, OK
            # ────────────────────────────────────────────────────────────────────────────────

            # Fallback: si alguna gráfica de motor (etf/crypto/eq) quedó vacía
            # (no hay ventas todavía), inyectar las posiciones abiertas unrealized
            try:
                positions = client.get_all_positions()
                for pos in positions:
                    cid = str(getattr(pos, 'asset_id', '')) or 'pos'
                    meta_engine = "etf"  # las posiciones no tienen prefix, default ETF
                    unrealized = float(pos.unrealized_pl) if pos.unrealized_pl else 0.0
                    if unrealized == 0:
                        continue
                    # Detectar motor por símbolo
                    sym = pos.symbol
                    if '/' in sym or sym in ('BTC', 'ETH', 'SOL', 'DOGE', 'AVAX', 'LTC'):
                        meta_engine = "crypto"
                    elif sym in ('NVDA','AMD','MARA','RIOT','TSLA','PLTR','SOFI','RIVN','LCID','GME','AMC'):
                        meta_engine = "eq"

                    if not new_cache[meta_engine]:  # solo si está vacío
                        import datetime as _dt
                        date_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
                        new_cache[meta_engine].append({"date": date_str, "equity": round(unrealized, 2), "engine": meta_engine})
            except Exception as pos_err:
                logger.debug(f"[Charts] Error calculando fallback posiciones: {pos_err}")

            _CHART_CACHE = new_cache
        except Exception as e:
            logger.error(f"[API] Error reconstruyendo Motor de Gráficas: {e}")
            
        await asyncio.sleep(60) # Recalcular cada 1 minuto de forma indetectable

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(_build_charts_task())
    asyncio.create_task(_weekly_scoring_task())
    asyncio.create_task(_daily_mode_refresh_task())

async def _weekly_scoring_task():
    """Job semanal de scoring (Propuesta C). Corre cada domingo 18:00 UTC."""
    while True:
        try:
            from engine.stock_scorer import get_scorer
            scorer = get_scorer()
            # Verificar si ya hay scores del día (evita doble cálculo en reinicios)
            existing = scorer.get_top_scores(limit=1)
            if existing:
                from datetime import datetime, timezone
                last_ts = datetime.fromisoformat(existing[0]["timestamp"])
                age_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
                if age_hours < 24:
                    logger.info(f"[Scoring] Scores recientes ({age_hours:.1f}h). Saltando.")
                    await asyncio.sleep(3600)  # revisar en 1h
                    continue
            logger.info("[Scoring] Iniciando cálculo semanal de scores (Propuesta C)...")
            scores = await scorer.score_universe()
            top5 = list(scores.items())[:5]
            logger.info(f"[Scoring] Top 5: {top5}")
        except Exception as e:
            logger.error(f"[Scoring] Error en el job semanal: {e}")
        await asyncio.sleep(6 * 3600)  # Correr cada 6 horas (ligero, usa caché de 24h)

async def _daily_mode_refresh_task():
    """Refresca el modo A/B/C al inicio y cada medianoche UTC."""
    while True:
        try:
            from engine.daily_mode import DailyModeManager
            DailyModeManager().refresh()
        except Exception as e:
            logger.debug(f"[DailyMode] Error en refresh: {e}")
        await asyncio.sleep(3600)  # verificar cada hora (sin impacto)

# R\u00e9gimen de mercado compartido entre el api_server y main.py
_regime_manager_instance = None

def _get_or_create_regime_manager():
    global _regime_manager_instance
    if _regime_manager_instance is None:
        try:
            from engine.regime_manager import RegimeManager
            _regime_manager_instance = RegimeManager()
        except Exception:
            pass
    return _regime_manager_instance

# ============================================================
# HELPER: PARSER ROBUSTO DE client_order_id
# ============================================================
import re as _re

# Mapa de prefijo → nombre del motor
_ENGINE_MAP = {
    "strat": "etf",
    "cry":   "crypto",
    "eq":    "equities",
}

# UUID8: exactamente 8 caracteres hexadecimales al final
_UUID8_PATTERN = _re.compile(r'^[0-9a-f]{8}$')
# Modo: mA, mB, mC
_MODE_PATTERN  = _re.compile(r'^m[ABC]$')

def parse_order_meta(client_order_id: str) -> dict:
    """
    Parsea un client_order_id robusto sin importar cuántos tokens tiene el nombre.

    Formatos soportados:
      - Legado:  {prefix}_{name}_{uuid8}           → ej: strat_GoldenCross_a3f8b2c1
      - Nuevo:   {prefix}_{name}_{mode}_{uuid8}    → ej: strat_GoldenCross_mA_a3f8b2c1
      - Crypto:  cry_{name}_{uuid8}
      - Equities: eq_{name}_{uuid8}

    Retorna:
      {
        "prefix":   "strat" | "cry" | "eq",
        "engine":   "etf" | "crypto" | "equities",
        "name":     nombre completo de la estrategia,
        "mode":     "A" | "B" | "C" | "LEGACY",
        "uuid":     los últimos 8 chars del id,
      }
    """
    if not client_order_id:
        return {"prefix": "unknown", "engine": "unknown", "name": "Manual", "mode": "LEGACY", "uuid": ""}

    raw = str(client_order_id)
    parts = raw.split("_")

    if len(parts) < 2:
        return {"prefix": "unknown", "engine": "unknown", "name": raw, "mode": "LEGACY", "uuid": ""}

    prefix = parts[0]
    engine = _ENGINE_MAP.get(prefix, "unknown")

    # Detectar si el último token es un UUID8
    last = parts[-1]
    if _UUID8_PATTERN.match(last):
        uuid_part = last
        inner = parts[1:-1]  # todo entre prefix y uuid
    else:
        uuid_part = ""
        inner = parts[1:]    # sin uuid

    # Detectar si el penúltimo token (antes del UUID) es un modo mA/mB/mC
    mode = "LEGACY"
    if inner and _MODE_PATTERN.match(inner[-1]):
        mode = inner[-1][1]  # "A", "B" o "C"
        name_parts = inner[:-1]
    else:
        name_parts = inner

    name = "_".join(name_parts) if name_parts else "unknown"

    return {
        "prefix":  prefix,
        "engine":  engine,
        "name":    name,
        "mode":    mode,
        "uuid":    uuid_part,
    }


# ============================================================
# ENDPOINTS DE DATOS
# ============================================================

@app.get("/api/market-regime")
async def get_market_regime():
    """Retorna el r\u00e9gimen actual del mercado y qu\u00e9 bots est\u00e1n activos por motor."""
    try:
        from engine.regime_manager import get_current_regime, REGIME_ETF_MAP, REGIME_CRYPTO_MAP, REGIME_EQUITIES_MAP, Regime
        state = get_current_regime()

        regime_str = state.get("regime", "UNKNOWN")
        try:
            regime = Regime(regime_str)
        except Exception:
            regime = Regime.UNKNOWN

        return {
            "regime": regime_str,
            "spy_price": state.get("spy_price", 0),
            "spy_sma200": state.get("spy_sma200", 0),
            "vix_price": state.get("vix_price", 0),
            "last_assessed": state.get("last_assessed"),
            "active_strategies": {
                "etf": REGIME_ETF_MAP.get(regime, []),
                "crypto": REGIME_CRYPTO_MAP.get(regime, []),
                "equities": REGIME_EQUITIES_MAP.get(regime, []),
            },
            "description": {
                "BULL": "\u2601\ufe0f Mercado alcista \u2014 Estrategias de momentum activas",
                "BEAR": "\ud83d\udfe5 Mercado bajista \u2014 Solo estrategias defensivas activas",
                "CHOP": "\ud83d\udd04 Mercado lateral \u2014 Grids y arbitraje activos",
                "UNKNOWN": "\u2753 Sin clasificar \u2014 Modo conservador",
            }.get(regime_str, "Sin datos")
        }
    except Exception as e:
        logger.error(f"[API] Error en /api/market-regime: {e}")
        return {"regime": "UNKNOWN", "error": str(e)}


@app.get("/api/daily-mode")
async def get_daily_mode():
    """
    Retorna el modo activo (A/B/C), el schedule de los próximos 7 días,
    y si hay un override manual activo.
    
    Modos:
      A = market-environment-analysis (Régimen SPY/VIX)
      B = market-news-analyst (Filtro de Noticias)
      C = us-stock-analysis (Scoring Dinámico)
    """
    try:
        from engine.daily_mode import get_mode_meta, DailyModeManager
        meta = get_mode_meta()
        if not meta:
            # Primera vez: inicializar el manager
            DailyModeManager()
            meta = get_mode_meta()
        return meta
    except Exception as e:
        logger.error(f"[API] Error en /api/daily-mode: {e}")
        return {"mode": "A", "error": str(e)}


@app.get("/api/news-filter")
async def get_news_filter_status():
    """
    Retorna el estado del filtro de noticias (Propuesta B):
    - Cache de noticias consultadas
    - Nivel de riesgo por símbolo
    - Edad de cada entrada en cache
    Solo relevante cuando el Modo B está activo.
    """
    try:
        from engine.daily_mode import get_active_mode
        from engine.news_risk_filter import get_news_filter
        active_mode = get_active_mode()
        cache = get_news_filter().get_cache_status()
        return {
            "active": active_mode == "B",
            "mode":   active_mode,
            "cache":  cache,
            "note":   "Solo activo en Modo B. En otros modos el filtro está desconectado."
        }
    except Exception as e:
        logger.error(f"[API] Error en /api/news-filter: {e}")
        return {"active": False, "cache": [], "error": str(e)}


@app.get("/api/stock-scores")
async def get_stock_scores(limit: int = 20, min_score: float = 0):
    """
    Retorna el ranking de acciones calculado por el StockScorer (Propuesta C).
    
    Params:
      limit:    Top N acciones a retornar (default 20)
      min_score: Filtrar solo acciones con score >= min_score
    
    Solo es dinámico cuando el Modo C está activo.
    En modo A/B retorna los scores calculados previamente (si existen).
    """
    try:
        from engine.daily_mode import get_active_mode
        from engine.stock_scorer import get_scorer
        active_mode = get_active_mode()
        scorer = get_scorer()
        all_scores = scorer.get_top_scores(limit=limit)
        filtered = [s for s in all_scores if s["score"] >= min_score]
        return {
            "mode_active": active_mode,
            "mode_c_active": active_mode == "C",
            "count": len(filtered),
            "scores": filtered,
            "note": "Modo C activo: scorer actualiza cada 6h. Otros modos: usa último cálculo disponible."
        }
    except Exception as e:
        logger.error(f"[API] Error en /api/stock-scores: {e}")
        return {"mode_active": "?", "count": 0, "scores": [], "error": str(e)}


@app.get("/api/account")
async def get_account():
    """Retorna balance, equity, P&L y buying power de la cuenta dinámicamente."""
    try:
        client = get_trading_client()
        acc = client.get_account()
        
        equity = float(acc.equity)
        last_equity = float(acc.last_equity) if acc.last_equity else equity
        pnl_day = equity - last_equity
        pnl_day_pct = (pnl_day / last_equity * 100) if last_equity > 0 else 0

        return {
            "equity": equity,
            "cash": float(acc.cash),
            "buying_power": float(acc.buying_power),
            "portfolio_value": float(acc.portfolio_value),
            "pnl_day": pnl_day,
            "pnl_day_pct": round(pnl_day_pct, 2),
            "total_pnl": equity - last_equity,
            "status": acc.status.value if acc.status else "active",
            "currency": acc.currency,
            "daytrade_count": getattr(acc, 'daytrade_count', 0),
            "pattern_day_trader": getattr(acc, 'pattern_day_trader', False),
            "long_market_value": float(acc.long_market_value) if hasattr(acc, 'long_market_value') and acc.long_market_value else 0.0,
            "short_market_value": float(acc.short_market_value) if hasattr(acc, 'short_market_value') and acc.short_market_value else 0.0,
        }
    except Exception as e:
        logger.error(f"[API] Error obteniendo cuenta: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clock")
async def get_clock():
    """Retorna el estado del mercado del servidor de Alpaca"""
    try:
        client = get_trading_client()
        clock = client.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open.isoformat() if hasattr(clock, 'next_open') and clock.next_open else None,
            "next_close": clock.next_close.isoformat() if hasattr(clock, 'next_close') and clock.next_close else None
        }
    except Exception as e:
        logger.error(f"[API] Error obteniendo el reloj: {e}")
        return {}


@app.get("/api/positions")
async def get_positions():
    """Retorna todas las posiciones abiertas."""
    try:
        client = get_trading_client()
        positions = client.get_all_positions()
        return [
            {
                "symbol":        p.symbol,
                "qty":           float(p.qty),
                "side":          p.side.value,
                "avg_entry":     float(p.avg_entry_price),
                "current_price": float(p.current_price) if p.current_price else 0,
                "market_value":  float(p.market_value) if p.market_value else 0,
                "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else 0,
                "unrealized_plpc": float(p.unrealized_plpc) * 100 if p.unrealized_plpc else 0,
            }
            for p in positions
        ]
    except Exception as e:
        logger.error(f"[API] Error obteniendo posiciones: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orders")
async def get_orders():
    """Retorna las últimas 30 órdenes (todas las que incluye filled/cancelled)."""
    try:
        client = get_trading_client()
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=100)
        )
        return [
            {
                "id":           str(o.id),
                "symbol":       o.symbol,
                "side":         o.side.value,
                "type":         o.order_type.value if o.order_type else "market",
                "qty":          float(o.qty) if o.qty else 0,
                "filled_qty":   float(o.filled_qty) if o.filled_qty else 0,
                "status":       o.status.value,
                "limit_price":  float(o.limit_price) if o.limit_price else None,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "filled_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "created_at":   o.submitted_at.isoformat() if o.submitted_at else None,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                # client_id completo para que el frontend pueda filtrar por prefijo (cry_, eq_, strat_)
                "client_id":    str(o.client_order_id) if o.client_order_id else "",
                "strategy":     str(o.client_order_id).split("_")[1] if (o.client_order_id and len(str(o.client_order_id).split("_")) > 1) else "Manual",
            }
            for o in orders
        ]
    except Exception as e:
        logger.error(f"[API] Error obteniendo órdenes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


import requests

from datetime import timedelta, timezone
@app.get("/api/history")
async def get_history(period: str = "1M", engine: str = "home"):
    """Retorna la historia de PNL sectorizada asíncrona en lugar del Total de Alpaca."""
    try:
        global _CHART_CACHE
        if engine not in _CHART_CACHE:
            return []
            
        history = _CHART_CACHE[engine]
        if not history:
            return []
            
        # Filtro de periodo
        now = datetime.now(timezone.utc)
        if period == "1D": threshold = now - timedelta(days=1)
        elif period == "1W": threshold = now - timedelta(days=7)
        elif period == "1A": threshold = now - timedelta(days=365)
        else: threshold = now - timedelta(days=30)  # max logic applied in 1M
        
        filtered = []
        for h in history:
            # Recrea datetime
            try:
                dt = datetime.strptime(h["date"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                if dt >= threshold:
                    filtered.append(h)
            except:
                pass
                
        return filtered
    except Exception as e:
        logger.error(f"[API] Error obteniendo historia custom: {e}")
        return []

@app.get("/api/symbol/history/{symbol}")
async def get_symbol_history(symbol: str, period: str = "1D"):
    """Retorna historial de barras para un símbolo específico (para mini-charts)."""
    try:
        client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
        
        if period == "1D":
            tf = TimeFrame.Minute
            qty = 390 # un día de mercado aprox
        else:
            tf = TimeFrame.Day
            qty = 30
            
        request_params = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            limit=qty
        )
        bars = client.get_stock_bars(request_params)
        
        return [
            {
                "t": b.timestamp.isoformat(),
                "c": b.close
            }
            for b in bars[symbol]
        ]
    except Exception as e:
        logger.error(f"[API] Error historia símbolo: {e}")
        return []


@app.get("/api/strategy/stats")
async def get_strategy_stats():
    """Retorna estadísticas agrupadas por estrategia.
    
    Mejoras Fase 17:
    - Usa parse_order_meta() para obtener el nombre correcto (fix bug parts[1])
    - Añade campo 'engine' (etf/crypto/equities) por estrategia
    - Añade 'mode_breakdown' {A: N, B: N, C: N, LEGACY: N} por estrategia
    """
    try:
        client = get_trading_client()
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1000)
        )

        stats = {}
        tracker = {}

        # Filtrar órdenes de nuestros bots y ordenar cronológicamente
        valid_orders = [
            o for o in orders
            if o.client_order_id and any(
                str(o.client_order_id).startswith(p)
                for p in ("strat_", "cry_", "eq_")
            )
        ]
        valid_orders.sort(
            key=lambda x: (x.filled_at or x.created_at or datetime.min.replace(tzinfo=__import__('datetime').timezone.utc))
        )

        for o in valid_orders:
            # Fase 17: usar parser robusto en vez de parts[1]
            meta = parse_order_meta(o.client_order_id)
            strat_name = meta["name"]
            engine     = meta["engine"]
            mode       = meta["mode"]   # "A", "B", "C" o "LEGACY"

            if strat_name not in stats:
                stats[strat_name] = {
                    "trades": 0,
                    "filled": 0,
                    "volume": 0.0,
                    "symbol": o.symbol,
                    "realized_pnl": 0.0,
                    "engine": engine,
                    # Desglose por modo: cuántas órdenes se ejecutaron en cada propuesta
                    "mode_breakdown": {"A": 0, "B": 0, "C": 0, "LEGACY": 0},
                }

            stats[strat_name]["trades"] += 1

            # Contabilizar en qué modo se ejecutó esta orden
            mode_key = mode if mode in ("A", "B", "C") else "LEGACY"
            stats[strat_name]["mode_breakdown"][mode_key] += 1

            if o.status.value == "filled":
                stats[strat_name]["filled"] += 1
                qty   = float(o.filled_qty) if o.filled_qty else 0
                price = float(o.filled_avg_price) if o.filled_avg_price else 0
                vol   = qty * price
                stats[strat_name]["volume"] += vol

                # Calcular Realized P&L
                tracker_key = f"{strat_name}_{o.symbol}"
                if tracker_key not in tracker:
                    tracker[tracker_key] = {"pos": 0.0, "avg": 0.0}

                pos = tracker[tracker_key]["pos"]
                avg = tracker[tracker_key]["avg"]

                if o.side.value == "buy":
                    new_cost = (pos * avg) + vol
                    pos += qty
                    avg = new_cost / pos if pos > 0 else 0
                    tracker[tracker_key] = {"pos": pos, "avg": avg}
                else:
                    realized = (price - avg) * qty
                    stats[strat_name]["realized_pnl"] += realized
                    pos -= qty
                    if pos <= 0:
                        pos = 0.0
                        avg = 0.0
                    tracker[tracker_key] = {"pos": pos, "avg": avg}

        # Redondear P&L
        for s in stats.values():
            s["realized_pnl"] = round(s["realized_pnl"], 4)
            s["volume"] = round(s["volume"], 2)

        return stats
    except Exception as e:
        logger.error(f"[API] Error obteniendo stats de estrategias: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/reports")
async def download_report(strategy: str = "all", period: str = "weekly", engine_filter: str = "all"):
    """Descarga el reporte de operaciones en CSV.
    
    Params:
      strategy:      nombre de estrategia | 'all'
      period:        'weekly' (7d) | 'monthly' (30d) | 'historical' (todo) | 'all' (sin l\u00edmite)
      engine_filter: 'all' | 'etf' | 'crypto' | 'equities'
    """
    try:
        from datetime import timezone, timedelta
        client = get_trading_client()
        # Fase 5: l\u00edmite ampliado a 1000 para cubrir hist\u00f3rico completo
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1000)
        )

        # Umbrales de tiempo
        now = datetime.now(timezone.utc)
        if period == "weekly":      threshold = now - timedelta(days=7)
        elif period == "monthly":   threshold = now - timedelta(days=30)
        elif period == "historical": threshold = None   # sin l\u00edmite
        else:                       threshold = None    # 'all' tambi\u00e9n sin l\u00edmite

        # Filtrar por estado filled + periodo + motor + estrategia
        filtered = []
        for o in orders:
            if o.status.value != "filled":
                continue
            # Filtrar por motor
            meta = parse_order_meta(o.client_order_id)
            if engine_filter != "all" and meta["engine"] != engine_filter:
                continue
            # Filtrar por estrategia
            if strategy != "all" and meta["name"].lower() != strategy.lower():
                continue
            # Filtrar por period
            if threshold and o.filled_at and o.filled_at < threshold:
                continue
            filtered.append((o, meta))

        # Ordenar cronol\u00f3gicamente para tracking de P\u0026L correcto
        filtered.sort(key=lambda x: x[0].filled_at or datetime.min.replace(tzinfo=timezone.utc))

        output = io.StringIO()
        writer = csv.writer(output)
        # Fase 4: Nuevas columnas Motor, Modo, Propuesta
        writer.writerow([
            "Fecha (UTC)", "Estrategia", "Motor", "Modo (A/B/C)", "Propuesta Activa",
            "Simbolo", "Lado", "Cantidad", "Precio ($)", "Volumen ($)",
            "P&L Realizado ($)", "ID Orden"
        ])

        # Mapa de modo a nombre de propuesta
        PROPOSAL_NAMES = {
            "A": "market-environment-analysis (R\u00e9gimen)",
            "B": "market-news-analyst (Filtro Noticias)",
            "C": "us-stock-analysis (Scoring Din\u00e1mico)",
            "LEGACY": "Legado (sin modo registrado)",
        }

        tracker = {}
        for o, meta in filtered:
            qty   = float(o.filled_qty) if o.filled_qty else 0
            price = float(o.filled_avg_price) if o.filled_avg_price else 0
            date_str = o.filled_at.strftime("%Y-%m-%d %H:%M:%S") if o.filled_at else ""
            vol   = round(qty * price, 2)

            strat_name = meta["name"]
            engine     = meta["engine"]
            mode       = meta["mode"]        # "A", "B", "C" o "LEGACY"
            proposal   = PROPOSAL_NAMES.get(mode, mode)

            # Calcular P&L realizado
            tracker_key = f"{strat_name}_{o.symbol}"
            if tracker_key not in tracker:
                tracker[tracker_key] = {"pos": 0.0, "avg": 0.0}

            pos = tracker[tracker_key]["pos"]
            avg = tracker[tracker_key]["avg"]
            realized_pnl = 0.0

            if o.side.value == "buy":
                new_cost = (pos * avg) + vol
                pos += qty
                avg = new_cost / pos if pos > 0 else 0
                tracker[tracker_key] = {"pos": pos, "avg": avg}
            else:
                realized_pnl = round((price - avg) * qty, 4)
                pos -= qty
                if pos <= 0:
                    pos = 0.0
                    avg = 0.0
                tracker[tracker_key] = {"pos": pos, "avg": avg}

            writer.writerow([
                date_str, strat_name, engine, mode, proposal,
                o.symbol, o.side.value.upper(), qty, price, vol,
                realized_pnl if o.side.value == "sell" else "-",
                str(o.id)
            ])

        output.seek(0)
        filename = f"reporte_{strategy}_{engine_filter}_{period}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"[API] Error generando reporte CSV: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategy/history/{strategy}")
async def get_strategy_history(strategy: str, period: str = "1M"):
    """Retorna la curva de P&L de una estrategia específica calculando desde sus órdenes."""
    try:
        client = get_trading_client()
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        )
        
        strat_orders = []
        for o in orders:
            if o.status.value != "filled" or not o.client_order_id:
                continue
            if str(o.client_order_id).startswith(f"strat_{strategy}"):
                strat_orders.append(o)
                
        strat_orders.sort(key=lambda x: x.filled_at)
        
        history = []
        realized_pnl = 0.0
        position = 0.0
        avg_entry = 0.0
        
        for o in strat_orders:
            qty = float(o.filled_qty) if o.filled_qty else 0
            price = float(o.filled_avg_price) if o.filled_avg_price else 0
            
            if o.side.value == "buy":
                new_cost = position * avg_entry + qty * price
                position += qty
                avg_entry = new_cost / position if position > 0 else 0
            else:
                profit = (price - avg_entry) * qty
                realized_pnl += profit
                position -= qty
                if position <= 0:
                    position = 0
                    avg_entry = 0
                    
            history.append({
                "date": o.filled_at.strftime("%Y-%m-%d %H:%M"),
                "pnl": realized_pnl
            })
            
        # Slicing the history array based on period
        from datetime import timezone
        now = datetime.now(timezone.utc)
        if period == "1D": threshold = now - timedelta(days=1)
        elif period == "1W": threshold = now - timedelta(days=7)
        elif period == "1A": threshold = now - timedelta(days=365)
        else: threshold = now - timedelta(days=30)  # default 1M
        
        filtered = [h for h in history if datetime.strptime(h["date"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) >= threshold]
        
        return filtered
    except Exception as e:
        logger.error(f"[API] Error obteniendo historia de estrategia: {e}")
        return []

@app.get("/api/logs")
async def get_logs(limit: int = 100):
    """Retorna las últimas N líneas del log del engine."""
    try:
        log_file = Path(LOG_PATH)
        if not log_file.exists():
            return []

        lines = log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        recent = lines[-limit:] if len(lines) > limit else lines

        entries = []
        for line in reversed(recent):
            try:
                entry = json.loads(line)
                entries.append(entry)
            except:
                entries.append({"time": "", "level": "INFO", "source": "system", "msg": line})
        return entries
    except Exception as e:
        logger.error(f"[API] Error leyendo logs: {e}")
        return []


@app.get("/api/health")
async def health():
    """Health check."""
    return {"status": "ok", "engine": "AlpacaNode v2.0", "paper": PAPER}


# ============================================================
# ENDPOINTS EQUITIES ENGINE
# ============================================================

@app.get("/api/equities/status")
async def get_equities_status():
    """Estado del EquitiesEngine: estrategias activas, is_running, etc."""
    try:
        from main_equities import get_eq_engine_status
        return get_eq_engine_status()
    except Exception as e:
        return {"error": str(e), "is_running": False, "strategies": []}


@app.get("/api/equities/regime")
async def get_equities_regime():
    """Régimen de mercado actual (BULL/BEAR/CHOP) + datos de SPY y VIX."""
    try:
        from engine.regime_manager import get_current_regime
        return get_current_regime()
    except Exception as e:
        return {"error": str(e), "regime": "UNKNOWN"}


@app.get("/api/equities/universe")
async def get_equities_universe():
    """Universo dinámico del día: gainers y losers filtrados."""
    try:
        from engine.screener import get_daily_universe
        return get_daily_universe()
    except Exception as e:
        return {"error": str(e), "gainers": [], "losers": [], "all": []}


@app.get("/api/equities/portfolio")
async def get_equities_portfolio():
    """Estado del portfolio: equity, ATH, drawdown, circuit breaker."""
    try:
        from engine.portfolio_manager import get_portfolio_status
        return get_portfolio_status()
    except Exception as e:
        return {"error": str(e), "equity": 0, "is_halted": False}


@app.get("/api/equities/orders")
async def get_equities_orders():
    """Órdenes del día filtradas con prefijo 'eq_'."""
    try:
        client = get_trading_client()
        request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=50)
        orders = client.get_orders(request)
        eq_orders = [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value if o.side else "?",
                "qty": str(o.qty),
                "status": o.status.value if o.status else "?",
                "type": o.type.value if o.type else "?",
                "client_id": str(o.client_order_id or ""),
                "created_at": o.created_at.isoformat() if o.created_at else "",
                "filled_avg_price": str(o.filled_avg_price or ""),
            }
            for o in orders
            if str(o.client_order_id or "").startswith("eq_")
        ]
        return eq_orders
    except Exception as e:
        logger.error(f"[API] Error obteniendo órdenes de equities: {e}")
        return []


# ============================================================
# SERVIR EL DASHBOARD HTML (ruta raíz)
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Sirve el dashboard HTML interactivo."""
    dashboard_path = Path(__file__).parent / "static/index.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse(content=f"<h1>Dashboard cargando... ({dashboard_path} no encontrado)</h1><script>setTimeout(()=>location.reload(),3000)</script>")


# ============================================================
# FUNCIÓN PARA INICIAR DESDE main.py
# ============================================================
def start_api_server(host: str = "0.0.0.0", port: int = 8000):
    """Inicia el servidor API en un thread de background."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")
