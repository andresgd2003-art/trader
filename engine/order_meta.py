"""
engine/order_meta.py
=====================
Parser puro de client_order_id sin dependencias de Alpaca.
Se extrae aquí para poder importarse en tests sin necesitar las keys.

El api_server.py importa parse_order_meta desde aquí.
"""
import re
from typing import Optional

# Mapa de prefijo → nombre del motor
ENGINE_MAP = {
    "strat": "etf",
    "cry":   "crypto",
    "eq":    "equities",
}

_UUID8_PATTERN = re.compile(r'^[0-9a-f]{8}$', re.IGNORECASE)
_MODE_PATTERN  = re.compile(r'^m[ABC]$', re.IGNORECASE)


def parse_order_meta(raw: Optional[str]) -> dict:
    """
    Extrae prefix, engine, name, mode y uuid de un client_order_id.

    Formatos soportados:
      strat_{name}_{uuid8}                 → LEGACY, etf
      strat_{name}_{mA|mB|mC}_{uuid8}     → modo A/B/C, etf
      cry_{name}_{uuid8}                   → LEGACY, crypto
      cry_{name}_{mA|mB|mC}_{uuid8}        → modo A/B/C, crypto
      eq_{name}_{uuid8}                    → LEGACY, equities
      eq_{name}_{mA|mB|mC}_{uuid8}         → modo A/B/C, equities
      Manual_xxx                           → nombre manual, unknown
    """
    if not raw:
        return {"prefix": "unknown", "engine": "unknown", "name": "Manual", "mode": "LEGACY", "uuid": ""}

    parts = raw.split("_")

    if len(parts) < 2:
        return {"prefix": "unknown", "engine": "unknown", "name": raw, "mode": "LEGACY", "uuid": ""}

    prefix = parts[0]
    engine = ENGINE_MAP.get(prefix, "unknown")

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
        _ignored_mode = inner[-1][1].upper()  # backward compat: skip mode token
        name_parts = inner[:-1]
    else:
        name_parts = inner

    name = "_".join(name_parts) if name_parts else "unknown"

    # Mapeo: client_order_id name (sin espacios) → nombre legible de la estrategia
    # Incluye nombres truncados legacy Y nombres completos actuales
    LEGACY_NAME_MAP = {
        # ETF — legacy truncados (10 chars)
        "RSI+VIXFil":   "RSI + VIX Filter",
        "PairsTradi":   "Pairs Trading",
        "RSIBuytheD":   "RSI Buy the Dip",
        "MACDTrend":    "MACD Trend",
        "BollingerR":   "Bollinger Reversion",
        "GoldenCros":   "Golden Cross",
        "DonchianBr":   "Donchian Breakout",
        "MomentumRo":   "Momentum Rotation",
        "GridTradin":   "Grid Trading",
        "VWAPBounce":   "VWAP Bounce",
        # ETF — nombres completos sin espacios (formato actual)
        "RSIBuytheDip":         "RSI Buy the Dip",
        "GoldenCross":          "Golden Cross",
        "DonchianBreakout":     "Donchian Breakout",
        "BollingerReversion":   "Bollinger Reversion",
        "PairsTrading":         "Pairs Trading",
        "MomentumRotation":     "Momentum Rotation",
        "GridTrading":          "Grid Trading",
        "VWAPBounce":           "VWAP Bounce",
        "InverseMomentumETF":   "Inverse Momentum ETF",
        # Crypto — nombres completos sin espacios
        "EMATrendCrossover":           "EMA Trend Crossover",
        "BBBreakout":                  "BB Breakout",
        "DynamicSpotGrid":             "Dynamic Spot Grid",
        "SmartTWAPAccum":              "Smart TWAP Accum",
        "FundingSqueeze":              "Funding Squeeze",
        "VolumeAnomaly":               "Volume Anomaly",
        "PairDivergence":              "Pair Divergence",
        "EMARibbonPullback":           "EMA Ribbon Pullback",
        "VWAPTouch-and-Go":            "VWAP Touch-and-Go",
        "CryptoSentiment":             "Crypto Sentiment",
        "Micro-VWAPAVAXAggressive":    "Micro-VWAP AVAX Aggressive",
        "CryptoMeanReversionExtreme":  "Crypto Mean Reversion Extreme",
        # Equities — nombres completos sin espacios
        "DefensiveRotation": "Defensive Rotation",
        "GammaSqueeze":      "Gamma Squeeze",
        "SectorRotation":    "Sector Rotation",
    }
    if name in LEGACY_NAME_MAP:
        name = LEGACY_NAME_MAP[name]

    return {
        "prefix": prefix,
        "engine": engine,
        "name":   name,
        "mode":   mode,
        "uuid":   uuid_part,
    }

def compute_trade_pnls(orders: list, target_engine: str = None) -> list:
    """
    Replay FIFO sobre órdenes filled → lista de trades cerrados
    con {strategy, symbol, pnl, pct_return, closed_at}.
    """
    tracker = {}
    trades = []

    for o in orders:
        status = o.status.value if hasattr(o, "status") and hasattr(o.status, "value") else getattr(o, "status", None)
        if status != "filled":
            continue

        cid = str(getattr(o, "client_order_id", ""))
        if not cid.startswith("strat_") and not cid.startswith("cry_") and not cid.startswith("eq_"):
            continue

        meta = parse_order_meta(cid)
        engine = meta["engine"]
        if target_engine and engine != target_engine and engine != "unknown":
            continue

        strat_name = meta["name"]
        sym = getattr(o, "symbol", "unknown")
        tracker_key = f"{strat_name}_{sym}"
        
        if tracker_key not in tracker:
            tracker[tracker_key] = {"pos": 0.0, "avg": 0.0}

        qty = float(getattr(o, "filled_qty", 0) or 0)
        price = float(getattr(o, "filled_avg_price", 0) or 0)
        vol = qty * price
        
        side = getattr(o, "side", None)
        side_val = side.value if hasattr(side, "value") else str(side)

        pos = tracker[tracker_key]["pos"]
        avg = tracker[tracker_key]["avg"]

        if side_val == "buy":
            new_cost = (pos * avg) + vol
            pos += qty
            avg = new_cost / pos if pos > 0 else 0
            tracker[tracker_key] = {"pos": pos, "avg": avg}
        elif side_val == "sell":
            realized = (price - avg) * qty
            pct_return = (price / avg - 1) * 100 if avg > 0 else 0.0
            pos -= qty
            if pos <= 0:
                pos = 0.0
                avg = 0.0
            tracker[tracker_key] = {"pos": pos, "avg": avg}
            
            dt = getattr(o, "filled_at", getattr(o, "created_at", None))
            trades.append({
                "strategy": strat_name,
                "engine": engine,
                "symbol": sym,
                "pnl": realized,
                "pct_return": pct_return,
                "closed_at": dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
            })

    return trades

def compute_metrics(trade_pnls: list) -> dict:
    """
    Agrega métricas: wins, losses, win_rate, profit_factor,
    avg_win, avg_loss, sharpe, max_drawdown, trade_count_closed.
    Trades <10 → sharpe=None (evita ruido estadístico).
    """
    wins = 0
    losses = 0
    sum_win = 0.0
    sum_loss = 0.0
    pct_returns = []
    
    cumulative_pnl = 0.0
    peak = 0.0
    max_dd = 0.0

    for t in trade_pnls:
        pnl = t["pnl"]
        pct_returns.append(t["pct_return"])
        
        if pnl > 0:
            wins += 1
            sum_win += pnl
        elif pnl < 0:
            losses += 1
            sum_loss += abs(pnl)
            
        cumulative_pnl += pnl
        if cumulative_pnl > peak:
            peak = cumulative_pnl
        else:
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd

    trade_count_closed = wins + losses
    win_rate = (wins / trade_count_closed) if trade_count_closed > 0 else None
    profit_factor = (sum_win / sum_loss) if sum_loss > 0 else (None if sum_win == 0 else float('inf'))
    
    avg_win = (sum_win / wins) if wins > 0 else 0.0
    avg_loss = (sum_loss / losses) if losses > 0 else 0.0
    
    sharpe_trade = None
    if len(pct_returns) >= 10:
        mean_ret = sum(pct_returns) / len(pct_returns)
        variance = sum((x - mean_ret) ** 2 for x in pct_returns) / len(pct_returns)
        if variance > 0:
            import math
            std_ret = math.sqrt(variance)
            sharpe_trade = mean_ret / std_ret

    if profit_factor == float('inf'):
        profit_factor = None
            
    return {
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "sharpe": sharpe_trade,
        "max_drawdown": max_dd,
        "trade_count_closed": trade_count_closed,
        "sample_size_flag": trade_count_closed < 10
    }

