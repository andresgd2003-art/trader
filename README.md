# AlpacaNode Trading Engine

Bot de trading algorítmico con 10 estrategias en paralelo sobre la Paper Trading API de Alpaca. Desplegado en Docker/Easypanel.

## 🚀 Stack Tecnológico

- **Python 3.11** + `alpaca-py` (SDK oficial)
- **TA-Lib** para indicadores técnicos (RSI, MACD, Bollinger)
- **asyncio** — arquitectura Central Dispatcher (1 WebSocket)
- **SQLite** — persistencia de trades
- **FastAPI** — API del dashboard
- **Docker** + Easypanel

## 📁 Estructura

```
app/
├── main.py                    # Motor principal (WebSocket + Dispatcher)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example               # Plantilla de variables (NO subas .env)
├── engine/
│   ├── base_strategy.py       # Clase abstracta base
│   ├── order_manager.py       # Gestor de órdenes con rate-limiting
│   └── logger.py              # Logger JSON con rotación
└── strategies/
    ├── strat_01_macross.py    # Golden Cross (SMA 50/200)
    ├── strat_02_donchian.py   # Donchian Breakouts
    ├── strat_03_rotation.py   # Momentum Rotation semanal
    ├── strat_04_macd.py       # MACD Trend
    ├── strat_05_rsi_dip.py    # RSI Buy the Dip
    ├── strat_06_bollinger.py  # Bollinger Reversion
    ├── strat_07_vix_filter.py # RSI + Filtro VIX
    ├── strat_08_vwap.py       # VWAP Bounce intraday
    ├── strat_09_pairs.py      # Pairs Trading QQQ/XLK
    └── strat_10_grid.py       # Grid Trading
```

## ⚙️ Variables de Entorno

Copia `.env.example` a `.env` y configura:

```env
ALPACA_API_KEY=tu_key_aqui
ALPACA_SECRET_KEY=tu_secret_aqui
PAPER_TRADING=True
```

## 🐳 Deploy con Docker

```bash
# Prueba local
docker-compose up --build

# Ver logs
docker logs alpaca-trader -f
```

## 📊 Las 10 Estrategias

| # | Nombre | Activo | Tipo |
|---|--------|--------|------|
| 1 | Golden Cross | SMH | Tendencia |
| 2 | Donchian Breakout | QQQ | Tendencia |
| 3 | Momentum Rotation | Multi-ETF | Momentum |
| 4 | MACD Trend | XLK | Tendencia |
| 5 | RSI Buy the Dip | TQQQ | Reversión |
| 6 | Bollinger Reversion | SRVR | Reversión |
| 7 | RSI + VIX Filter | SPY | Reversión |
| 8 | VWAP Bounce | SMH | Intraday |
| 9 | Pairs Trading | QQQ/XLK | Arbitraje |
| 10 | Grid Trading | SOXX | Grid |

> ⚠️ Este proyecto opera en **Paper Trading** (dinero ficticio). No hay riesgo real.
