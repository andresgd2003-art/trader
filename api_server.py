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
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
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
            }
            for o in orders
        ]
    except Exception as e:
        logger.error(f"[API] Error obteniendo órdenes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def get_history():
    """Retorna la historia del portafolio para el gráfico de equity."""
    try:
        client = get_trading_client()
        history = client.get_portfolio_history(
            history_filter=GetPortfolioHistoryRequest(
                period="1M",
                timeframe="1D",
                extended_hours=False
            )
        )
        result = []
        for i, ts in enumerate(history.timestamp):
            equity = history.equity[i] if i < len(history.equity) else 0
            pl = history.profit_loss[i] if history.profit_loss and i < len(history.profit_loss) else 0
            if equity and equity > 0:
                result.append({
                    "date":   datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                    "equity": equity,
                    "pl":     pl or 0,
                })
        return result
    except Exception as e:
        logger.error(f"[API] Error obteniendo historia: {e}")
        return []   # No fallar si no hay datos aún


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
