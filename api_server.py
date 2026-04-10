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
# ENDPOINTS DE DATOS
# ============================================================

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
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=30)
        )
        return [
            {
                "id":         str(o.id),
                "symbol":     o.symbol,
                "side":       o.side.value,
                "type":       o.order_type.value if o.order_type else "market",
                "qty":        float(o.qty) if o.qty else 0,
                "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                "status":     o.status.value,
                "limit_price":  float(o.limit_price) if o.limit_price else None,
                "filled_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                "strategy":     str(o.client_order_id).split("_")[1] if (o.client_order_id and (str(o.client_order_id).startswith("strat_") or str(o.client_order_id).startswith("cry_"))) else "Manual",
            }
            for o in orders
        ]
    except Exception as e:
        logger.error(f"[API] Error obteniendo órdenes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


import requests

from datetime import timedelta
@app.get("/api/history")
async def get_history(period: str = "1M"):
    """Retorna la historia del portafolio con mayor flexibilidad de periodos."""
    try:
        url = "https://paper-api.alpaca.markets/v2/account/portfolio/history" if PAPER else "https://api.alpaca.markets/v2/account/portfolio/history"
        headers = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}
        
        # Mapa de resolución óptima para Alpaca
        res_map = {
            "1D": "5Min",
            "1W": "1H",
            "1M": "1D",
            "3M": "1D",
            "1A": "1W"
        }
        tf = res_map.get(period, "1D")
        
        params = {
            "period": period,
            "timeframe": tf,
            "extended_hours": "true" if period == "1D" else "false"
        }
        
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        
        if "timestamp" not in data or not data["timestamp"]:
            return []
            
        result = []
        for i, ts in enumerate(data["timestamp"]):
            equity = data["equity"][i] if i < len(data["equity"]) else 0
            if equity and equity > 0:
                result.append({
                    "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                    "equity": equity,
                    "profit": data["profit_loss"][i] if "profit_loss" in data else 0
                })
        return result
    except Exception as e:
        logger.error(f"[API] Error en historia: {e}")
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
        logger.error(f"[API] Error historia símbolo {symbol}: {e}")
        return []


@app.get("/api/strategy/stats")
async def get_strategy_stats():
    """Retorna estadísticas agrupadas por estrategia"""
    try:
        client = get_trading_client()
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        )
        
        stats = {}
        tracker = {}
        
        # Sort first to calculate PNL correctly
        valid_orders = [o for o in orders if o.client_order_id and str(o.client_order_id).startswith("strat_")]
        valid_orders.sort(key=lambda x: (x.filled_at if x.filled_at else x.created_at) if (x.filled_at or x.created_at) else datetime.min)
        
        for o in valid_orders:
            strat_name = str(o.client_order_id).split("_")[1]
            if strat_name not in stats:
                stats[strat_name] = {"trades": 0, "filled": 0, "volume": 0.0, "symbol": o.symbol, "realized_pnl": 0.0}
            
            stats[strat_name]["trades"] += 1
            if o.status.value == "filled":
                stats[strat_name]["filled"] += 1
                qty = float(o.filled_qty) if o.filled_qty else 0
                price = float(o.filled_avg_price) if o.filled_avg_price else 0
                vol = qty * price
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

        return stats
    except Exception as e:
        logger.error(f"[API] Error obteniendo stats de estrategias: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/reports")
async def download_report(strategy: str = "all", period: str = "weekly"):
    """Descarga el reporte de operaciones en CSV para una estrategia."""
    try:
        client = get_trading_client()
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        )
        
        filtered = []
        for o in orders:
            is_match = False
            if strategy == "all":
                is_match = True
            elif o.client_order_id and str(o.client_order_id).startswith(f"strat_{strategy}"):
                is_match = True
                
            if is_match and o.status.value == "filled":
                filtered.append(o)
                
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Fecha (UTC)", "Estrategia", "Simbolo", "Lado", "Cantidad", "Precio ($)", "Volumen ($)", "P&L Realizado ($)", "ID Orden"])
        
        from datetime import timezone, timedelta
        now = datetime.now(timezone.utc)
        if period == "weekly": threshold = now - timedelta(days=7)
        elif period == "monthly": threshold = now - timedelta(days=30)
        else: threshold = now - timedelta(days=3650) # all
        
        # Diccionario para trackear la posicion y avg_entry por símbolo y estrategia
        tracker = {}
        
        # Ordenar chronológicamente para el tracking de P&L
        filtered.sort(key=lambda x: x.filled_at)
        
        for o in filtered:
            if o.filled_at and o.filled_at < threshold:
                continue
                
            strat_name = str(o.client_order_id).split("_")[1] if (o.client_order_id and str(o.client_order_id).startswith("strat_")) else "Manual"
            qty = float(o.filled_qty) if o.filled_qty else 0
            price = float(o.filled_avg_price) if o.filled_avg_price else 0
            date = o.filled_at.strftime("%Y-%m-%d %H:%M:%S") if o.filled_at else ""
            vol = round(qty * price, 2)
            
            # Calcular ganancia
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
                
            writer.writerow([date, strat_name, o.symbol, o.side.value.upper(), qty, price, vol, realized_pnl if o.side.value == "sell" else "-", str(o.id)])
            
        output.seek(0)
        filename = f"reporte_{strategy}_{period}.csv"
        
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
