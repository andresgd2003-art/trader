# Auditoría 2026-04-28

## Estado inicial
- Equity paper: $102,406
- PnL día: -$213.21 (-0.21%)
- 6 posiciones huérfanas en equities (GammaSqueeze cuarentenado)
- IWM en ETF: -$68 sin SL forzoso
- TelegramNotifier síncrono (bloqueaba el trading loop)
- 3 estrategias ETF sin SL/TP forzosos con -$121 acumulado

---

## Cambios deployados

### Commit f5349a8 — fix(critical)
**Archivos modificados:**
- `engine/notifier.py` — TelegramNotifier reescrito como non-blocking
- `strategies/strat_02_donchian.py` — SL/TP forzoso a IWM

**Detalles:**

#### TelegramNotifier (engine/notifier.py)
- Problema: `requests.post(timeout=5)` síncrono llamado desde paths críticos del trading loop
- Solución: Worker thread daemon con deque de 200 mensajes, throttle 25 msg/min, retry automático en 429
- Resultado: Notificaciones no bloquean ejecución de órdenes

#### Donchian Breakout (strategies/strat_02_donchian.py)
- Problema: IWM en -$68 (-1.1%) sin SL hasta romper canal de 60 min
- Solución: `FORCED_STOP_LOSS_PCT=0.02` (-2%) y `FORCED_TAKE_PROFIT_PCT=0.03` (+3%)
- Entry price sincronizado desde Alpaca al arrancar

#### Liquidación huérfanos (ejecución directa vía Alpaca API)
| Símbolo | Acción | Order ID |
|---------|--------|----------|
| AMC     | SELL   | 3afe98de |
| BBBY    | SELL   | bb4c6bab |
| CLOV    | SELL   | 3b896ec4 |
| GME     | SELL   | fbd0b8e6 |
| RIVN    | SELL   | 4c89ef6c |
| WKHS    | SELL   | 2b70d60f |

---

### Commit 3149e56 — fix(strategies): FORCED SL/TP a 3 ETF perdedoras
**Archivos modificados:**
- `strategies/strat_04_macd.py` — MACD Trend (DIA)
- `strategies/strat_01_macross.py` — Golden Cross (XLC)
- `strategies/strat_06_bollinger.py` — Bollinger Reversion (QQQ)

**Contexto:** Estas 3 estrategias heredadas generaron -$121 PnL acumulado:
| Estrategia        | PnL realizado | Trades cerrados | Win rate |
|-------------------|---------------|-----------------|----------|
| Bollinger Rev.    | -$74.02       | 13              | 61.5%    |
| MACD Trend        | -$32.32       | 26              | 19.2%    |
| Golden Cross      | -$15.38       | 1               | —        |

**Causa raíz:** Sin SL forzoso, las posiciones aguantaban pérdidas hasta señales técnicas tardías (MACD recross, Death Cross → puede tardar días/semanas).

**Fixes aplicados:**
| Estrategia        | SL anterior | SL nuevo | TP nuevo |
|-------------------|-------------|----------|----------|
| MACD Trend (DIA)  | ninguno     | -1.5%    | +2.0%    |
| Golden Cross (XLC)| ninguno     | -1.5%    | +2.5%    |
| Bollinger Rev. (QQQ)| -1.5%    | -2.0%    | +2.0%    |

---

## Tests
```
29 passed, 3 warnings in 1.53s
```
Todos los tests pasaron antes de cada deploy.

---

## Estado final post-deploy
- Commit `3149e56` activo en VPS
- Servicio `alpacatrader.service`: active
- Posiciones equities huérfanas: 0 (liquidadas)
- Estrategias ETF sin SL: 0
- TelegramNotifier: non-blocking

---

## Pendiente
- MACD Trend (DIA): win-rate 19.2% — candidato a quarantine si persiste en pérdidas
- Bollinger Reversion (QQQ): profit_factor 0.33 — recalibrar STD_DEV o cambiar a BB_STD=2.5
- Posición IWM activa: -$68 — el SL forzoso -2% cortará en el próximo bar si persiste la caída
