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
  GET  /                → Dashboard HTML local
"""
import os
import json
import logging
import csv
import io
import asyncio
import requests
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Cargar variables de entorno con RUTA ABSOLUTA (Requisito PROMPT 18)
import os
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# CONFIGURACIÓN DE LOGGING (Timestamps PROMPT 18)
import logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [api_server] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("api_server")

# CONFIGURACIÓN (Auth Inquebrantable PROMPT 15)
API_KEY = os.getenv('APCA_API_KEY_ID')
SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')

if not API_KEY or not SECRET_KEY:
    logger.critical("[API] ❌ ERROR CRÍTICO: Las llaves de Alpaca son NULAS. Revisa el archivo .env")
else:
    logger.info(f"[API] Keys cargadas correctamente. Prefijo: {API_KEY[:4]}***")

LOG_PATH = os.environ.get("LOG_PATH", "/opt/trader/data/engine.log")

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
    """
    Obtiene el cliente de trading detectando automáticamente Paper/Live.
    Requisito PROMPT 18: PK->True, AK->False.
    """
    ak = os.getenv('APCA_API_KEY_ID') or os.getenv('ALPACA_API_KEY')
    sk = os.getenv('APCA_API_SECRET_KEY') or os.getenv('ALPACA_SECRET_KEY')
    
    # Detección automática de Paper/Live
    is_paper = True if ak and ak.startswith('PK') else False
    
    return TradingClient(api_key=ak, secret_key=sk, paper=is_paper)

# [P3 FIX - 2026-04-15] STATE_CACHE extendido para eliminar llamadas redundantes a Alpaca.
# Se agregaron 'orders_full' para historial completo y 'symbol_bars' para mini-charts sin HTTP 429.
STATE_CACHE = {
    "account": None,
    "positions": {"crypto": [], "etf": [], "eq": []},
    "orders": {"crypto": [], "etf": [], "eq": []},
    "clock": None,
    "orders_full": [],
    "symbol_bars": {},
}

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
            STATE_CACHE["orders_full"] = list(orders)
            logger.debug(f"[Cache] orders_full actualizado: {len(STATE_CACHE['orders_full'])} órdenes")

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
                    from engine.order_meta import parse_order_meta, compute_trade_pnls, compute_metrics
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

            # ─── HOME: Portfolio history vía REST directo ─────────────────────────────────
            # Usamos la API REST directamente porque get_portfolio_history() no existe
            # en todas las versiones del SDK alpaca-py
            try:
                import requests as _requests
                import datetime as _dt
                _ak = os.getenv('APCA_API_KEY_ID') or os.getenv('ALPACA_API_KEY')
                _sk = os.getenv('APCA_API_SECRET_KEY') or os.getenv('ALPACA_SECRET_KEY')
                _is_paper = True if _ak and _ak.startswith('PK') else False
                _base = 'https://paper-api.alpaca.markets' if _is_paper else 'https://api.alpaca.markets'
                _headers = {'APCA-API-KEY-ID': _ak, 'APCA-API-SECRET-KEY': _sk}
                _res = _requests.get(
                    f'{_base}/v2/account/portfolio/history',
                    headers=_headers,
                    params={'period': '1M', 'timeframe': '1D', 'extended_hours': 'false'},
                    timeout=10
                )
                if _res.status_code == 200:
                    _data = _res.json()
                    home_points = []
                    if _data.get('timestamp') and _data.get('equity'):
                        for ts, eq in zip(_data['timestamp'], _data['equity']):
                            if eq and eq > 0:
                                date_str = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
                                home_points.append({'date': date_str, 'equity': round(float(eq), 2), 'engine': 'home'})
                    if home_points:
                        new_cache['home'] = home_points
                        logger.info(f'[Charts] Home: {len(home_points)} puntos de portfolio history de Alpaca')
                else:
                    logger.warning(f'[Charts] Portfolio history REST error {_res.status_code}: {_res.text[:100]}')
            except Exception as ph_err:
                logger.warning(f'[Charts] No se pudo obtener portfolio history: {ph_err}. Usando calculo local.')
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

async def update_history_cache_task():
    """Background task to fetch portfolio history, minimizando llamadas API."""
    import requests
    while True:
        try:
            ak = os.getenv('APCA_API_KEY_ID') or os.getenv('ALPACA_API_KEY')
            sk = os.getenv('APCA_API_SECRET_KEY') or os.getenv('ALPACA_SECRET_KEY')
            if not ak or not sk:
                await asyncio.sleep(60)
                continue
                
            is_p = True if ak and ak.startswith('PK') else False
            base_url = "https://paper-api.alpaca.markets" if is_p else "https://api.alpaca.markets"
            url = f"{base_url}/v2/account/portfolio/history"
            
            headers = {"APCA-API-KEY-ID": ak, "APCA-API-SECRET-KEY": sk}
            history_cache = {}
            tf_map = {
                "1D": ("1D", "5Min"),
                "1W": ("1W", "15Min"),
                "1M": ("1M", "1D"),
                "1A": ("1A", "1D")
            }
            
            for p in ["1D", "1W", "1M", "1A"]:
                params = {"period": tf_map[p][0], "timeframe": tf_map[p][1], "extended_hours": "false"}
                res = requests.get(url, headers=headers, params=params, timeout=10)
                if res.status_code == 200:
                    history_data = res.json()
                    hist_objs = []
                    if "timestamp" in history_data and "equity" in history_data:
                        import datetime as _dt
                        for ts, eq in zip(history_data['timestamp'], history_data['equity']):
                            if eq is not None:
                                date_str = _dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
                                hist_objs.append({"date": date_str, "equity": round(float(eq), 2)})
                    history_cache[p] = hist_objs
            
            STATE_CACHE["history"] = history_cache
            
        except Exception as e:
            logger.error(f"[API] Error actualizando history cache: {e}")
            
        await asyncio.sleep(60)  # Fetch cada 60 seg, sin laggear al frontend

async def update_cache_task():
    """Background task to fetch account and positions, minimizing API calls."""
    while True:
        try:
            client = get_trading_client()
            
            # Account update
            try:
                acc = client.get_account()
                equity = float(acc.equity)
                last_equity = float(acc.last_equity) if acc.last_equity else equity
                
                STATE_CACHE["account"] = {
                    "equity": equity,
                    "cash": float(acc.cash),
                    "buying_power": float(acc.buying_power),
                    "portfolio_value": float(acc.portfolio_value),
                    "settled_cash": float(getattr(acc, 'settled_cash', 0.0)),
                    "pnl_day": equity - last_equity,
                    "pnl_day_pct": round((equity - last_equity) / last_equity * 100, 2) if last_equity > 0 else 0,
                    "status": acc.status.value if acc.status else "active",
                    "currency": acc.currency,
                }
            except Exception as acc_err:
                logger.error(f"[API] Error actualizando caché de CUENTA: {acc_err}")

            # Clock update
            try:
                clock = client.get_clock()
                STATE_CACHE["clock"] = {
                    "is_open": clock.is_open,
                    "next_open": clock.next_open.isoformat(),
                    "next_close": clock.next_close.isoformat(),
                    "timestamp": clock.timestamp.isoformat()
                }
            except Exception as clock_err:
                logger.error(f"[API] Error actualizando caché de RELOJ: {clock_err}")
            
            # Positions update & Categorization
            raw_positions = client.get_all_positions()
            categorized_pos = {"crypto": [], "etf": [], "eq": []}

            # Whitelist de ETFs conocidos (Todo lo demás a Equities)
            etf_symbols = {
                "SPY", "QQQ", "TQQQ", "SQQQ", "IWM", "DIA", "SMH", "SOXX", "SRVR",
                "XLK", "XLF", "XLV", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC", "XLP", "XLY",
                "QID", "SH", "PSQ", "VIXY", "BND", "AGG", "SHY"
            }

            # Pre-build symbol→strategy map from all cached filled equities orders
            # Uses STATE_CACHE["orders_full"] (up to 1000 orders) to avoid a redundant
            # Alpaca call and to cover positions opened before the 100-order window.
            symbol_to_strategy: dict = {}
            try:
                _all_orders = STATE_CACHE.get("orders_full") or []
                # Sort newest-first so the most recent strategy assignment wins
                _all_orders_sorted = sorted(
                    _all_orders,
                    key=lambda x: x.filled_at or x.created_at or datetime.min.replace(tzinfo=__import__('datetime').timezone.utc),
                    reverse=True
                )
                for _o in _all_orders_sorted:
                    _cid = str(_o.client_order_id or "")
                    if _cid.startswith("eq_") and _o.filled_qty and float(_o.filled_qty) > 0:
                        _meta = parse_order_meta(_cid)
                        _name = _meta.get("name", "")
                        if _name:
                            # Most-recent assignment wins (overwrite older ones)
                            symbol_to_strategy[_o.symbol] = _name
                # Orphan universe fallback: symbols in strategy universes
                # but without eq_ order history (pre-eq_ legacy positions).
                _SHORT_SQUEEZE_UNIVERSE = {
                    "GME","AMC","BBBY","MVIS","CLOV","WKHS","NKLA","RIDE","GOEV",
                    "LCID","RIVN","SPCE","SNDL","TLRY","ATER","CEI","PROG"
                }
                _HIGH_BETA_UNIVERSE = {
                    "NVDA","AMD","MARA","RIOT","TSLA","PLTR","SOFI","RIVN","LCID",
                    "MVIS","GME","AMC","BBBY","SNDL","ATER","CLOV","WKHS","NKLA","IDEX","CEI"
                }
                for p in raw_positions:
                    _sym = p.symbol
                    if _sym in symbol_to_strategy:
                        continue
                    if _sym in _SHORT_SQUEEZE_UNIVERSE:
                        symbol_to_strategy[_sym] = "GammaSqueeze (orphan)"
                    elif _sym in _HIGH_BETA_UNIVERSE:
                        symbol_to_strategy[_sym] = "VCP (orphan)"
            except Exception:
                pass

            for p in raw_positions:
                # Conversión explícita a diccionario (Blindaje de Serialización)
                pos_data = {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "side": p.side.value,
                    "avg_entry": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if hasattr(p, 'current_price') and p.current_price else 0,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else 0,
                    "unrealized_plpc": float(p.unrealized_plpc) * 100 if p.unrealized_plpc else 0,
                    "asset_class": p.asset_class.value
                }
                
                # Clasificación (Requisito PROMPT 16)
                is_crypto = p.asset_class.value == 'crypto' or '/USD' in p.symbol
                
                if is_crypto:
                    categorized_pos["crypto"].append(pos_data)
                elif p.symbol in etf_symbols:
                    categorized_pos["etf"].append(pos_data)
                else:
                    pos_data["strategy"] = symbol_to_strategy.get(p.symbol, "—")
                    categorized_pos["eq"].append(pos_data)

            STATE_CACHE["positions"] = categorized_pos
            
            # Orders update & Categorization
            try:
                from alpaca.trading.requests import GetOrdersRequest
                from alpaca.trading.enums import QueryOrderStatus
                raw_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=50))
                categorized_ords = {"crypto": [], "etf": [], "eq": []}
                
                for o in raw_orders:
                    ord_data = {
                        "id":           str(o.id),
                        "symbol":       o.symbol,
                        "side":         o.side.value,
                        "type":         o.order_type.value if o.order_type else "market",
                        "qty":          float(o.qty) if o.qty else 0,
                        "filled_qty":   float(o.filled_qty) if o.filled_qty else 0,
                        "notional":        float(o.notional) if o.notional else None,
                        "filled_notional": float(getattr(o, 'filled_notional', None) or 0) or None,
                        "status":       o.status.value,
                        "limit_price":  float(o.limit_price) if o.limit_price else None,
                        "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                        "created_at":   o.submitted_at.isoformat() if o.submitted_at else None,
                        "client_id":    str(o.client_order_id) if o.client_order_id else "",
                        "strategy":     symbol_to_strategy.get(o.symbol, "")
                    }
                    
                    # Usamos el meta parser para los órdenes que lo tengan, y si no, caemos a ETFs list
                    meta = parse_order_meta(ord_data["client_id"])
                    engine = meta.get("engine", "unknown")
                    
                    is_crypto = o.asset_class.value == 'crypto' or '/USD' in o.symbol or engine == "crypto"
                    
                    if is_crypto:
                        categorized_ords["crypto"].append(ord_data)
                    elif engine == "equities" or (engine == "unknown" and o.symbol not in etf_symbols):
                        categorized_ords["eq"].append(ord_data)
                    else:
                        categorized_ords["etf"].append(ord_data)
                
                STATE_CACHE["orders"] = categorized_ords
            except Exception as ord_err:
                logger.error(f"[API] Error actualizando caché de ÓRDENES: {ord_err}")

            logger.debug("[API] STATE_CACHE actualizada (Categorización Absoluta V2).")
        except Exception as e:
            logger.error(f"[API] Error updating STATE_CACHE: {e}")
        
        await asyncio.sleep(5)  # Fetch cada 5 segundos

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(update_cache_task())
    asyncio.create_task(update_history_cache_task())
    asyncio.create_task(_build_charts_task())
    asyncio.create_task(_weekly_scoring_task())
    # _daily_mode_refresh_task removed (A/B/C mode system eliminated)

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
# HELPER: Nombres de estrategias ACTUALMENTE registradas por motor
# ============================================================
_ACTIVE_STRATEGY_NAMES_CACHE: dict | None = None

def _extract_strategy_name_from_source(source: str) -> str | None:
    """Extrae el primer valor de `name=...` o primer literal pasado a super().__init__(...)."""
    import re as _re2
    # Caso 1: super().__init__(name="Foo", ...)
    m = _re2.search(r'super\(\)\.__init__\s*\([^)]*?name\s*=\s*["\']([^"\']+)["\']', source, _re2.DOTALL)
    if m:
        return m.group(1)
    # Caso 2: super().__init__("Foo", ...)
    m = _re2.search(r'super\(\)\.__init__\s*\(\s*["\']([^"\']+)["\']', source)
    if m:
        return m.group(1)
    return None

def _collect_names_from_engine(main_module_name: str, import_line_regex: str, strategies_package_dir: str) -> set:
    """
    Dado el módulo main (main.py / main_equities.py / main_crypto.py),
    extrae las clases importadas del paquete de estrategias y lee el `name` de cada archivo.
    """
    import re as _re2
    names: set = set()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    main_path = os.path.join(base_dir, main_module_name)
    pkg_dir = os.path.join(base_dir, strategies_package_dir)

    try:
        with open(main_path, "r", encoding="utf-8") as f:
            main_src = f.read()
    except Exception as e:
        logger.warning(f"[ActiveStrats] No pude leer {main_module_name}: {e}")
        return names

    # Extraer nombres de clase importados
    imported_classes: set = set()
    for m in _re2.finditer(import_line_regex, main_src, _re2.DOTALL):
        block = m.group(1)
        for cls in _re2.findall(r'([A-Z][A-Za-z0-9_]+)', block):
            imported_classes.add(cls)

    if not os.path.isdir(pkg_dir):
        return names

    # Para cada archivo strat_*.py en el pkg_dir, si define una clase importada, extraer name
    for fname in os.listdir(pkg_dir):
        if not fname.startswith("strat_") or not fname.endswith(".py"):
            continue
        fpath = os.path.join(pkg_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception:
            continue
        classes_in_file = set(_re2.findall(r'^class\s+([A-Za-z0-9_]+)\s*[\(:]', src, _re2.MULTILINE))
        if not (classes_in_file & imported_classes):
            continue
        name = _extract_strategy_name_from_source(src)
        if name:
            names.add(name)
    return names


def _get_active_strategy_names() -> dict:
    """
    Retorna dict {engine: set(names)} con las estrategias ACTUALMENTE registradas
    en main.py / main_equities.py / main_crypto.py. Cacheado a nivel de módulo.
    """
    global _ACTIVE_STRATEGY_NAMES_CACHE
    if _ACTIVE_STRATEGY_NAMES_CACHE is not None:
        return _ACTIVE_STRATEGY_NAMES_CACHE

    # ETF — main.py: from strategies import (...)
    etf = _collect_names_from_engine(
        "main.py",
        r'from\s+strategies\s+import\s+\(([^)]+)\)',
        "strategies",
    )
    # Equities — main_equities.py: from strategies_equities import (...)
    equities = _collect_names_from_engine(
        "main_equities.py",
        r'from\s+strategies_equities\s+import\s+\(([^)]+)\)',
        "strategies_equities",
    )
    # Crypto — main_crypto.py: imports individuales dentro de _register_strategies
    crypto = _collect_names_from_engine(
        "main_crypto.py",
        r'(from\s+strategies_crypto\.[^\n]+import[^\n]+(?:\n\s*from\s+strategies_crypto\.[^\n]+import[^\n]+)*)',
        "strategies_crypto",
    )
    # Fallback crypto: si el regex multilinea no agarró todo, escanear todas las importaciones sueltas
    if len(crypto) < 5:
        import re as _re2
        base_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            with open(os.path.join(base_dir, "main_crypto.py"), "r", encoding="utf-8") as f:
                mc_src = f.read()
            imported = set()
            for m in _re2.finditer(r'from\s+strategies_crypto\.\S+\s+import\s+([A-Za-z0-9_,\s]+)', mc_src):
                for cls in _re2.findall(r'([A-Z][A-Za-z0-9_]+)', m.group(1)):
                    imported.add(cls)
            pkg_dir = os.path.join(base_dir, "strategies_crypto")
            for fname in os.listdir(pkg_dir):
                if not (fname.startswith("strat_") and fname.endswith(".py")):
                    continue
                with open(os.path.join(pkg_dir, fname), "r", encoding="utf-8") as f:
                    src = f.read()
                classes_in_file = set(_re2.findall(r'^class\s+([A-Za-z0-9_]+)\s*[\(:]', src, _re2.MULTILINE))
                if classes_in_file & imported:
                    nm = _extract_strategy_name_from_source(src)
                    if nm:
                        crypto.add(nm)
        except Exception as e:
            logger.warning(f"[ActiveStrats] Fallback crypto falló: {e}")

    _ACTIVE_STRATEGY_NAMES_CACHE = {"etf": etf, "equities": equities, "crypto": crypto}
    logger.info(
        f"[ActiveStrats] Registradas — ETF={len(etf)} Equities={len(equities)} Crypto={len(crypto)}"
    )
    return _ACTIVE_STRATEGY_NAMES_CACHE


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

        # Lazy first-assess: si nunca se evalu\u00f3, forzar una evaluaci\u00f3n best-effort
        if state.get("last_assessed") is None:
            try:
                rm = _get_or_create_regime_manager()
                if rm is not None:
                    rm.assess()
                    state = get_current_regime()
            except Exception as _e_lazy:
                logger.warning(f"[API] Lazy regime assess fall\u00f3: {_e_lazy}")

        regime_str = state.get("regime", "UNKNOWN")
        try:
            regime = Regime(regime_str)
        except Exception:
            regime = Regime.UNKNOWN

        return {
            "regime": regime_str,
            "spy_price": state.get("spy_price", 0),
            "spy_sma50": state.get("spy_sma50", 0),
            "spy_sma20": state.get("spy_sma20", 0),
            "vix_price": state.get("vix_price", 0),
            "last_assessed": state.get("last_assessed"),
            "active_strategies": {
                "etf": REGIME_ETF_MAP.get(regime, []),
                "crypto": REGIME_CRYPTO_MAP.get(regime, []),
                "equities": REGIME_EQUITIES_MAP.get(regime, []),
            },
            "description": {
                "BULL": "☁️ Mercado alcista — Estrategias de momentum activas",
                "BEAR": "🟥 Mercado bajista — Solo estrategias defensivas activas",
                "CHOP": "🔄 Mercado lateral — Grids y arbitraje activos",
                "UNKNOWN": "❓ Sin clasificar — Modo conservador",
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
    return {"status": "removed", "message": "A/B/C mode rotation eliminated"}


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
        from engine.news_risk_filter import get_news_filter
        cache = get_news_filter().get_cache_status()
        return {
            "active": True,
            "cache":  cache,
        }
    except Exception as e:
        logger.error(f"[API] Error en /api/news-filter: {e}")
        return {"active": True, "cache": [], "error": str(e)}


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
        from engine.stock_scorer import get_scorer
        scorer = get_scorer()
        all_scores = scorer.get_top_scores(limit=limit)
        filtered = [s for s in all_scores if s["score"] >= min_score]
        return {
            "mode_c_active": True,
            "count": len(filtered),
            "scores": filtered,
        }
    except Exception as e:
        logger.error(f"[API] Error en /api/stock-scores: {e}")
        return {"mode_active": "?", "count": 0, "scores": [], "error": str(e)}


@app.get("/api/account")
async def get_account():
    """Retorna balance y equity desde STATE_CACHE (Latencia Cero - PROMPT 18)."""
    data = STATE_CACHE.get("account")
    return data if data else {}


@app.get("/api/clock")
async def get_clock():
    """Retorna el estado del mercado desde STATE_CACHE (Latencia Cero)."""
    data = STATE_CACHE.get("clock")
    if not data:
        return {"is_open": False, "status": "cache_warming"}
    return data


@app.get("/api/positions")
async def get_positions():
    """Retorna posiciones desde STATE_CACHE (Latencia Cero - PROMPT 18)."""
    data = STATE_CACHE.get("positions")
    return data if data else {"crypto": [], "etf": [], "eq": []}



@app.get("/api/orders")
async def get_orders():
    """Retorna órdenes desde STATE_CACHE (Latencia Cero - PROMPT 18)."""
    data = STATE_CACHE.get("orders")
    return data if data else {"crypto": [], "etf": [], "eq": []}


import requests

@app.get("/api/history")
async def get_history(period: str = "1M", engine: str = "home"):
    """
    Retorna la historia de patrimonio desde STATE_CACHE (Latencia Cero - PROMPT 18).
    Evita bloqueos síncronos al cambiar de pestañas en el dashboard.
    """
    history_cache = STATE_CACHE.get("history", {})
    return history_cache.get(period, [])

@app.get("/api/symbol/history/{symbol}")
async def get_symbol_history(symbol: str, period: str = "1D"):
    """Retorna historial de barras para un símbolo específico (para mini-charts). Con cache TTL 5min."""
    import time as _time
    cache_key = f"{symbol}:{period}"
    sb_cache = STATE_CACHE.setdefault("symbol_bars", {})
    cached = sb_cache.get(cache_key)
    if cached and (_time.time() - cached.get("ts", 0)) < 300:
        return cached["bars"]
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

        result = [
            {"t": b.timestamp.isoformat(), "c": b.close}
            for b in bars[symbol]
        ]
        sb_cache[cache_key] = {"bars": result, "ts": _time.time()}
        return result
    except Exception as e:
        logger.error(f"[API] Error historia símbolo: {e}")
        return []


@app.get("/api/strategy/stats")
async def get_strategy_stats(period: str = "today"):
    """Retorna estadísticas agrupadas por estrategia.
    period: 'today' | 'week' | 'month' | 'all'
    """
    from datetime import timezone, timedelta
    try:
        orders = STATE_CACHE.get("orders_full") or []
        if not orders:
            logger.warning("[API] orders_full cache vacío en strategy_stats, fallback a Alpaca")
            client = get_trading_client()
            orders = list(client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1000)
            ))

        # Calcular umbral de fecha según período
        now = datetime.now(timezone.utc)
        if period == "today":
            threshold = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            threshold = now - timedelta(days=7)
        elif period == "month":
            threshold = now - timedelta(days=30)
        else:
            threshold = None  # "all" — sin filtro

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

        # Aplicar filtro de período
        if threshold:
            valid_orders = [
                o for o in valid_orders
                if (o.filled_at or o.created_at or datetime.min.replace(tzinfo=timezone.utc)) >= threshold
            ]

        DEAD_STRATS = {
            "PEAD Earnings Drift", "NLP News Sentiment", "Insider Buying Flow",
            "Gapper Momentum", "Opening Gap Fade", "RSI Extreme Reversion", "Statistical Pairs Arb"
        }

        from engine.order_meta import parse_order_meta, compute_trade_pnls, compute_metrics
        for o in valid_orders:
            # Fase 17: usar parser robusto en vez de parts[1]
            meta = parse_order_meta(o.client_order_id)
            strat_name = meta["name"]
            
            if strat_name in DEAD_STRATS:
                continue
                
            engine     = meta["engine"]

            if strat_name not in stats:
                stats[strat_name] = {
                    "trades": 0,
                    "filled": 0,
                    "volume": 0.0,
                    "symbol": o.symbol,
                    "realized_pnl": 0.0,
                    "engine": engine,
                }

            stats[strat_name]["trades"] += 1

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

        # Calcular métricas avanzadas (Palanca 2)
        all_trade_pnls = compute_trade_pnls(valid_orders)
        trades_by_strat = {}
        for t in all_trade_pnls:
            trades_by_strat.setdefault(t["strategy"], []).append(t)

        for strat_name, s in stats.items():
            metrics = compute_metrics(trades_by_strat.get(strat_name, []))
            s.update(metrics)

        # Redondear P&L
        for s in stats.values():
            s["realized_pnl"] = round(s["realized_pnl"], 4)
            s["volume"] = round(s["volume"], 2)

        return stats
    except Exception as e:
        logger.error(f"[API] Error obteniendo stats de estrategias: {e}")
        raise HTTPException(status_code=500, detail=str(e))

_RANKING_CACHE = {"data": [], "ts": 0}

@app.get("/api/strategy/ranking")
async def get_strategy_ranking(sort_by: str = "profit_factor", desc: bool = True):
    """
    Retorna el ranking de estrategias con TTL de 60s
    """
    import time
    global _RANKING_CACHE
    
    try:
        from engine.order_meta import parse_order_meta, compute_trade_pnls, compute_metrics
        
        # Check cache
        if time.time() - _RANKING_CACHE["ts"] < 60 and _RANKING_CACHE["data"]:
            ranking_list = list(_RANKING_CACHE["data"])
        else:
            orders = STATE_CACHE.get("orders_full") or []
            if not orders:
                return []

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
            
            # Filtro de Paper Trading: ignorar órdenes de más de 14 días
            from datetime import timedelta
            now_utc = datetime.now(__import__('datetime').timezone.utc)
            cutoff = now_utc - timedelta(days=14)
            valid_orders = [o for o in valid_orders if (o.filled_at or o.created_at or datetime.min.replace(tzinfo=__import__('datetime').timezone.utc)) >= cutoff]
            
            # Calcular Realized PNL base
            stats = {}
            tracker = {}
            DEAD_STRATS = {
                "PEAD Earnings Drift", "NLP News Sentiment", "Insider Buying Flow",
                "Gapper Momentum", "Opening Gap Fade", "RSI Extreme Reversion", "Statistical Pairs Arb"
            }
            for o in valid_orders:
                meta = parse_order_meta(o.client_order_id)
                strat_name = meta["name"]
                
                if strat_name in DEAD_STRATS:
                    continue
                    
                engine = meta["engine"]
                if strat_name not in stats:
                    stats[strat_name] = {
                        "strategy": strat_name,
                        "engine": engine,
                        "symbol_focus": o.symbol,
                        "trades": 0,
                        "realized_pnl": 0.0
                    }
                stats[strat_name]["trades"] += 1
                if o.status.value == "filled":
                    qty = float(o.filled_qty) if o.filled_qty else 0
                    price = float(o.filled_avg_price) if o.filled_avg_price else 0
                    tracker_key = f"{strat_name}_{o.symbol}"
                    if tracker_key not in tracker: tracker[tracker_key] = {"pos": 0.0, "avg": 0.0}
                    pos = tracker[tracker_key]["pos"]
                    avg = tracker[tracker_key]["avg"]
                    if o.side.value == "buy":
                        new_cost = (pos * avg) + (qty * price)
                        pos += qty
                        avg = new_cost / pos if pos > 0 else 0
                        tracker[tracker_key] = {"pos": pos, "avg": avg}
                    else:
                        realized = (price - avg) * qty
                        stats[strat_name]["realized_pnl"] += realized
                        pos -= qty
                        if pos <= 0:
                            pos = 0.0; avg = 0.0
                        tracker[tracker_key] = {"pos": pos, "avg": avg}

            all_trade_pnls = compute_trade_pnls(valid_orders)
            trades_by_strat = {}
            for t in all_trade_pnls:
                trades_by_strat.setdefault(t["strategy"], []).append(t)

            ranking_list = []
            for strat_name, s in stats.items():
                metrics = compute_metrics(trades_by_strat.get(strat_name, []))
                s.update(metrics)
                s["realized_pnl"] = round(s["realized_pnl"], 4)
                ranking_list.append(s)
                
            _RANKING_CACHE = {"data": ranking_list, "ts": time.time()}

        # Sort the ranking list
        def get_sort_key(item):
            val = item.get(sort_by)
            if val is None:
                return float('-inf') if desc else float('inf')
            return val

        ranking_list.sort(key=get_sort_key, reverse=desc)

        # Filtrar estrategias que ya no están registradas en los motores actuales.
        # Normalizamos quitando espacios/underscores y bajando a minúsculas para que
        # "SmartTWAPAccum" (parseado del client_order_id) matchee "Smart TWAP Accum" (name de la clase).
        # También descartamos siempre los pseudo-nombres administrativos (Adopt_*, OrphanTrailStop_*).
        def _norm(s: str) -> str:
            return "".join(ch for ch in (s or "").lower() if ch.isalnum())

        ADMIN_PREFIXES = ("adopt_", "orphantrailstop_", "manualflat_")
        try:
            active = _get_active_strategy_names()
            active_norm = {eng: {_norm(n) for n in names} for eng, names in active.items()}
            filtered = []
            for item in ranking_list:
                nm = item.get("strategy") or item.get("name") or ""
                eng = item.get("engine", "unknown")
                if nm.lower().startswith(ADMIN_PREFIXES):
                    logger.warning(f"[Ranking] Excluyendo admin/huérfano: {nm} ({eng})")
                    continue
                active_set = active_norm.get(eng)
                if active_set is None or _norm(nm) in active_set:
                    filtered.append(item)
                else:
                    logger.warning(f"[Ranking] Excluyendo estrategia histórica: {nm} ({eng})")
            ranking_list = filtered
        except Exception as _e_filter:
            logger.warning(f"[Ranking] Filtro de estrategias activas falló: {_e_filter}")

        return ranking_list
    except Exception as e:
        logger.error(f"[API] Error en /api/strategy/ranking: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/strategy/compare")
async def get_strategy_compare(strategies: str, period: str = "1W"):
    """
    Retorna PnL normalizado para overlay de multiples estrategias
    """
    try:
        from engine.order_meta import parse_order_meta
        from datetime import timezone, timedelta
        
        strat_list = [s.strip() for s in strategies.split(",") if s.strip()]
        if not strat_list:
            return {}

        orders = STATE_CACHE.get("orders_full") or []
        
        now = datetime.now(timezone.utc)
        if period == "1D":
            threshold = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "1W":
            threshold = now - timedelta(days=7)
        elif period == "1M":
            threshold = now - timedelta(days=30)
        elif period == "1A":
            threshold = now - timedelta(days=365)
        else:
            threshold = None
            
        valid_orders = [
            o for o in orders
            if o.client_order_id and o.status.value == "filled"
        ]
        valid_orders.sort(
            key=lambda x: (x.filled_at or x.created_at or datetime.min.replace(tzinfo=timezone.utc))
        )
        
        if threshold:
            valid_orders = [
                o for o in valid_orders
                if (o.filled_at or o.created_at or datetime.min.replace(tzinfo=timezone.utc)) >= threshold
            ]
            
        result = {}
        for s_name in strat_list:
            result[s_name] = {"labels": [], "equity_curve": []}
            
        tracker = {}
        cum_pnls = {s: 0.0 for s in strat_list}
        
        for o in valid_orders:
            meta = parse_order_meta(o.client_order_id)
            strat_name = meta["name"]
            
            if strat_name not in strat_list:
                continue
                
            qty = float(o.filled_qty) if o.filled_qty else 0
            price = float(o.filled_avg_price) if o.filled_avg_price else 0
            
            tracker_key = f"{strat_name}_{o.symbol}"
            if tracker_key not in tracker:
                tracker[tracker_key] = {"pos": 0.0, "avg": 0.0}

            pos = tracker[tracker_key]["pos"]
            avg = tracker[tracker_key]["avg"]

            if o.side.value == "buy":
                new_cost = (pos * avg) + (qty * price)
                pos += qty
                avg = new_cost / pos if pos > 0 else 0
                tracker[tracker_key] = {"pos": pos, "avg": avg}
            else:
                realized = (price - avg) * qty
                cum_pnls[strat_name] += realized
                pos -= qty
                if pos <= 0:
                    pos = 0.0
                    avg = 0.0
                tracker[tracker_key] = {"pos": pos, "avg": avg}
                
                dt = o.filled_at or o.created_at
                dt_str = dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, "strftime") else str(dt)
                
                if realized != 0:
                    result[strat_name]["labels"].append(dt_str)
                    result[strat_name]["equity_curve"].append(round(cum_pnls[strat_name], 2))
                    
        return result
    except Exception as e:
        logger.error(f"[API] Error en /api/strategy/compare: {e}")
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
        orders = STATE_CACHE.get("orders_full") or []
        if not orders:
            logger.warning("[API] orders_full cache vacío en reports, fallback a Alpaca")
            client = get_trading_client()
            orders = list(client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1000)
            ))

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

        # Ordenar cronológicamente para tracking de P&L correcto
        filtered.sort(key=lambda x: x[0].filled_at or datetime.min.replace(tzinfo=timezone.utc))

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Fecha (UTC)", "Estrategia", "Motor",
            "Clase de Activo", "Simbolo", "Lado", "Cantidad", "Precio ($)", "Volumen ($)",
            "P&L Realizado ($)", "ID Orden"
        ])

        tracker = {}
        for o, meta in filtered:
            qty   = float(o.filled_qty) if o.filled_qty else 0
            price = float(o.filled_avg_price) if o.filled_avg_price else 0
            date_str = o.filled_at.strftime("%Y-%m-%d %H:%M:%S") if o.filled_at else ""
            vol   = round(qty * price, 2)
            
            # Extraer Asset Class de Alpaca
            asset_class = o.asset_class.value if hasattr(o, "asset_class") and o.asset_class else "unknown"

            strat_name = meta["name"]
            engine     = meta["engine"]

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
                date_str, strat_name, engine,
                asset_class, o.symbol, o.side.value.upper(), qty, price, vol,
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
        orders = STATE_CACHE.get("orders_full") or []
        if not orders:
            logger.warning("[API] orders_full cache vacío en strategy_history, fallback a Alpaca")
            client = get_trading_client()
            orders = list(client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1000)
            ))

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
    ak = os.getenv('APCA_API_KEY_ID') or os.getenv('ALPACA_API_KEY')
    is_paper = True if ak and ak.startswith('PK') else False
    return {"status": "ok", "engine": "AlpacaNode v2.0", "paper": is_paper}


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
        state = get_current_regime()
        if state.get("last_assessed") is None:
            try:
                rm = _get_or_create_regime_manager()
                if rm is not None:
                    rm.assess()
                    state = get_current_regime()
            except Exception as _e_lazy:
                logger.warning(f"[API] Lazy equities regime assess falló: {_e_lazy}")
        return state
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
        orders = STATE_CACHE.get("orders_full") or []
        if not orders:
            logger.warning("[API] orders_full cache vacío en equities_orders, fallback a Alpaca")
            client = get_trading_client()
            orders = list(client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1000)
            ))
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
