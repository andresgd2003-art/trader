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
    """Retorna balance, equity, P&L y buying power de la cuenta."""
    try:
        client = get_trading_client()
        acc = client.get_account()
        return {
            "equity":           float(acc.equity),
            "cash":             float(acc.cash),
            "buying_power":     float(acc.buying_power),
            "portfolio_value":  float(acc.portfolio_value),
            "initial_capital":  100_000.0,   # Capital inicial de paper trading
            "pnl":              float(acc.equity) - 100_000.0,
            "pnl_pct":          (float(acc.equity) - 100_000.0) / 100_000.0 * 100,
            "daytrade_count":   acc.daytrade_count,
            "status":           acc.status.value if acc.status else "active",
        }
    except Exception as e:
        logger.error(f"[API] Error obteniendo cuenta: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
                "filled_at":    o.filled_at.isoformat() if o.filled_at else None,
                "strategy":     str(o.client_order_id).split("_")[1] if (o.client_order_id and str(o.client_order_id).startswith("strat_")) else "Manual",
            }
            for o in orders
        ]
    except Exception as e:
        logger.error(f"[API] Error obteniendo órdenes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


import requests

@app.get("/api/history")
async def get_history():
    """Retorna la historia del portafolio para el gráfico de equity."""
    try:
        url = "https://paper-api.alpaca.markets/v2/account/portfolio/history" if PAPER else "https://api.alpaca.markets/v2/account/portfolio/history"
        headers = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}
        
        # Obtenemos historial del último mes, resolución diaria
        res = requests.get(url, headers=headers, params={"period": "1M", "timeframe": "1D"})
        data = res.json()
        
        if "timestamp" not in data or not data["timestamp"]:
            return []
            
        result = []
        for i, ts in enumerate(data["timestamp"]):
            equity = data["equity"][i] if i < len(data["equity"]) else 0
            pl = data["profit_loss"][i] if i < len(data["profit_loss"]) else 0
            
            if equity and equity > 0:
                result.append({
                    "date":   datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                    "equity": equity,
                    "pl":     pl or 0,
                })
        return result
    except Exception as e:
        logger.error(f"[API] Error obteniendo historia directa: {e}")
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
        for o in orders:
            if not o.client_order_id or not str(o.client_order_id).startswith("strat_"):
                continue
                
            strat_name = str(o.client_order_id).split("_")[1]
            if strat_name not in stats:
                stats[strat_name] = {"trades": 0, "filled": 0, "volume": 0.0, "symbol": o.symbol}
            
            stats[strat_name]["trades"] += 1
            if o.status.value == "filled":
                stats[strat_name]["filled"] += 1
                qty = float(o.filled_qty) if o.filled_qty else 0
                price = float(o.filled_avg_price) if o.filled_avg_price else 0
                stats[strat_name]["volume"] += qty * price

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
        writer.writerow(["Fecha", "Estrategia", "Simbolo", "Lado", "Cantidad", "Precio", "Volumen USD", "ID Orden"])
        
        for o in filtered:
            strat_name = str(o.client_order_id).split("_")[1] if (o.client_order_id and str(o.client_order_id).startswith("strat_")) else "Manual"
            qty = float(o.filled_qty) if o.filled_qty else 0
            price = float(o.filled_avg_price) if o.filled_avg_price else 0
            date = o.filled_at.isoformat() if o.filled_at else ""
            writer.writerow([date, strat_name, o.symbol, o.side.value.upper(), qty, price, round(qty * price, 2), str(o.id)])
            
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
    return {"status": "ok", "engine": "AlpacaNode v1.0", "paper": PAPER}


# ============================================================
# SERVIR EL DASHBOARD HTML (ruta raíz)
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Sirve el dashboard HTML interactivo."""
    dashboard_path = Path("/app/static/index.html")
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard cargando...</h1><script>setTimeout(()=>location.reload(),3000)</script>")


# ============================================================
# FUNCIÓN PARA INICIAR DESDE main.py
# ============================================================
def start_api_server(host: str = "0.0.0.0", port: int = 8000):
    """Inicia el servidor API en un thread de background."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")
