# GUIA DE CORRECCION — AlpacaNode Trading Bot
> Auditoría Multi-Agente · Versión 1.0 · Fecha: 2026-04-15

---

## RESUMEN EJECUTIVO

El sistema AlpacaNode es un bot de trading con **3 engines independientes** (ETF/Index, Crypto, Equities),
**30 estrategias activas**, un servidor de dashboard FastAPI y despliegue en VPS Hostinger.

### Estado Actual Diagnosticado

| Severidad | Cantidad | Descripción resumida |
|-----------|----------|----------------------|
| 🔴 CRÍTICO | 4 | Bot no compra por fallos de lógica en filtros y parámetros API |
| 🟠 ALTO | 3 | Duplicados en órdenes, SMA200 mal calculado, cash settlement |
| 🟡 MEDIO | 4 | Telegram roto, timestamps faltantes, pre-market incompleto, WebSocket único |
| 🟢 BAJO | 2 | Grid flag sin persistencia, logging inconsistente |

### Bugs que impiden comprar (prioridad absoluta)
1. **VIXY vs VIX** — umbral $30 con VIXY a $29.45 = siempre en modo pánico
2. **Price filter contradiction** — screener max $25 + scorer min $150 = universo vacío
3. **notional en LIMIT orders** — HTTP 400 de Alpaca en toda orden del Grid
4. **settled_cash = 0.0** en paper trading = sizing bloqueado

---

## ARQUITECTURA DEL SISTEMA MULTI-AGENTE

### Principios de Operación
- Cada agente tiene **acceso de lectura** a todos los archivos de contexto en `/AUDITORIA/`
- Los agentes se ejecutan **secuencialmente por fase** salvo donde se indique paralelismo
- Cada fase termina con un **checkpoint de verificación** antes de avanzar
- Se pueden **crear agentes adicionales** dinámicamente si una fase revela problemas no documentados
- Todos los agentes reportan sus hallazgos en archivos de log dentro de `/AUDITORIA/logs/`

### Archivos de Contexto Disponibles para Todos los Agentes
```
/AUDITORIA/AUDITORIA_COMPLETA.txt      — Diagnóstico técnico completo (45 fases)
/AUDITORIA/HISTORIAL_COMPLETO.txt      — Historial de correcciones aplicadas
/AUDITORIA/Hardening Alpaca Cash Account Trading.md  — Mejoras de hardening
/AUDITORIA/GUIA DE CORRECCION.md       — Este documento (referencia de plan)
```

### Mapa de Agentes

```
┌─────────────────────────────────────────────────────────────┐
│                    COORDINADOR PRINCIPAL                     │
│              (Claude Code — este proceso)                    │
└─────────────────────────────────────────────────────────────┘
         │               │               │               │
    FASE 0          FASE 1          FASE 2          FASE 3
    Baseline        VPS             Código          Runtime
    ─────────       ─────────       ─────────       ─────────
    Agente_00       Agente_VPS      Agente_Code     Agente_Runtime
    (snapshot)      (infra)         (repo audit)    (live monitor)
                                        │
                              Sub-agentes por bug:
                              Agente_Fix_VIXY
                              Agente_Fix_Prices
                              Agente_Fix_Notional
                              Agente_Fix_Cash
                              Agente_Fix_SMA200
                              Agente_Fix_Grid
                              Agente_Fix_Telegram
                              [+ agentes bajo demanda]
         │               │               │               │
    FASE 4          FASE 5          FASE 6
    Correcciones    Validación      Monitoreo
    ─────────       ─────────       ─────────
    [Fix agents]    Agente_Val      Agente_Monitor
                    (e2e tests)     (ongoing)
```

---

## FASE 0 — BASELINE (Estado de Partida)

**Objetivo:** Capturar el estado actual del sistema antes de cualquier cambio.
**Agente:** `Agente_Baseline`
**Duración estimada:** 15 minutos
**Prerrequisito:** Ninguno

### Tareas del Agente_Baseline

#### 0.1 Snapshot del repositorio
```bash
# Ejecutar en VPS
git log --oneline -20                        # Últimos 20 commits
git status                                   # Archivos modificados sin commit
git diff HEAD                                # Cambios no commiteados
git stash list                               # Stashes pendientes
```

#### 0.2 Estado del sistema en VPS
```bash
systemctl status alpacanode-main             # Engine ETF
systemctl status alpacanode-crypto           # Engine Crypto
systemctl status alpacanode-equities         # Engine Equities
ps aux | grep python                         # Procesos Python activos
```

#### 0.3 Inventario de variables de entorno
```bash
# Verificar que .env existe y tiene todas las keys
cat /app/.env | grep -v "SECRET\|KEY"        # Sin exponer secrets
python3 -c "from dotenv import dotenv_values; print(list(dotenv_values('/app/.env').keys()))"
```

#### 0.4 Versiones de dependencias
```bash
pip freeze > /tmp/baseline_requirements.txt
python3 --version
```

#### 0.5 Guardar snapshot de archivos críticos
```bash
# Hash de archivos críticos para detectar cambios posteriores
md5sum engine/regime_manager.py
md5sum engine/order_manager.py
md5sum engine/order_manager_equities.py
md5sum strategies/strat_10_grid.py
md5sum strategies_equities/strat_02_vcp.py
md5sum main.py main_crypto.py main_equities.py
```

### Checkpoint FASE 0 ✅
- [ ] Últimos 20 commits registrados
- [ ] Estado de los 3 servicios systemd anotado
- [ ] Todos los servicios tienen `.env` cargado correctamente
- [ ] No hay procesos zombie Python (`ps aux | grep python | grep -v grep | wc -l` ≤ 3)
- [ ] Archivo `baseline_requirements.txt` guardado

> **Si algún checkpoint falla:** Investigar antes de continuar. Un servicio caído o procesos zombie
> indican un problema de infraestructura que debe resolverse primero.

---

## FASE 1 — AUDITORÍA DE INFRAESTRUCTURA VPS

**Objetivo:** Verificar que el VPS está configurado correctamente y los servicios son estables.
**Agente:** `Agente_VPS`
**Duración estimada:** 20-30 minutos
**Prerrequisito:** FASE 0 completada

### Tareas del Agente_VPS

#### 1.1 Recursos del sistema
```bash
free -h                                      # RAM disponible
df -h                                        # Espacio en disco
top -bn1 | head -20                          # CPU y memoria por proceso
ulimit -a                                    # Límites del sistema
```

#### 1.2 Conectividad de red
```bash
# Alpaca API endpoints
curl -s -o /dev/null -w "%{http_code}" https://api.alpaca.markets/v2/account \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY"

# Verificar latencia
ping -c 5 api.alpaca.markets
curl -s https://data.alpaca.markets/v2/stocks/bars/latest?symbols=SPY \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" | python3 -m json.tool
```

#### 1.3 Configuración systemd
```bash
cat /etc/systemd/system/alpacanode-main.service
cat /etc/systemd/system/alpacanode-crypto.service
cat /etc/systemd/system/alpacanode-equities.service
# Verificar: EnvironmentFile apunta a .env correcto
# Verificar: WorkingDirectory correcto
# Verificar: Restart=on-failure configurado
```

#### 1.4 Análisis de logs recientes (últimas 24h)
```bash
journalctl -u alpacanode-main --since "24 hours ago" | grep -E "ERROR|CRITICAL|WARNING" | tail -50
journalctl -u alpacanode-crypto --since "24 hours ago" | grep -E "ERROR|CRITICAL" | tail -50
journalctl -u alpacanode-equities --since "24 hours ago" | grep -E "ERROR|CRITICAL" | tail -50
```

#### 1.5 Verificar rate limiting de Alpaca
```bash
# Contar llamadas API en los últimos logs
journalctl -u alpacanode-main --since "1 hour ago" | grep -c "429"
journalctl -u alpacanode-crypto --since "1 hour ago" | grep -c "429"
```

#### 1.6 Puertos y firewall
```bash
netstat -tlnp | grep python                  # Puertos abiertos por bot
# Dashboard debería estar en 8000
curl -s http://localhost:8000/api/account | python3 -m json.tool
curl -s http://localhost:8000/api/positions | python3 -m json.tool
```

#### 1.7 Verificar Docker (si aplica)
```bash
docker ps -a                                 # Contenedores activos/parados
docker logs alpacanode-main --tail 50        # Si usa Docker
```

### Checkpoint FASE 1 ✅
- [ ] RAM libre > 512MB
- [ ] Disco libre > 5GB
- [ ] Alpaca API responde con HTTP 200
- [ ] Los 3 servicios systemd tienen `EnvironmentFile` apuntando a ruta absoluta de `.env`
- [ ] No hay errores HTTP 429 en la última hora
- [ ] Dashboard responde en `localhost:8000/api/account`
- [ ] No más de 3 procesos Python activos simultáneamente

> **Agentes adicionales si falla:** Crear `Agente_VPS_Network` para diagnóstico de red o
> `Agente_VPS_Cleanup` para limpiar procesos zombie y reiniciar servicios.

---

## FASE 2 — AUDITORÍA DEL REPOSITORIO (Código)

**Objetivo:** Identificar y documentar todos los bugs en el código fuente.
**Agente:** `Agente_Code`
**Duración estimada:** 30-45 minutos
**Prerrequisito:** FASE 1 completada

### 2.A — Bugs Críticos a Verificar (BLOQUEAN COMPRAS)

#### 2.A.1 Bug VIXY vs VIX — `regime_manager.py`
```python
# BUSCAR esta línea:
grep -n "VIX_BULL_THRESHOLD\|VIXY\|vix_threshold\|30\." engine/regime_manager.py

# PROBLEMA: El código compara precio de VIXY ETF (~$29) con umbral de VIX (~30)
# VIXY cotiza en rango $25-$35, VIX real cotiza en rango 10-80
# RESULTADO: Bot siempre detecta "pánico" y bloquea compras

# VERIFICAR si ya usa yfinance para ^VIX real:
grep -n "yfinance\|download\|\^VIX" engine/regime_manager.py
```

**Estado esperado después del fix:**
- `VIX_BULL_THRESHOLD` ≥ 25.0 si se usa VIXY
- O código usa `yfinance.download("^VIX")` para el VIX real

#### 2.A.2 Bug Price Filter — `screener.py` + `stock_scorer.py`
```python
# BUSCAR umbrales de precio:
grep -n "MAX_PRICE\|MIN_PRICE\|price.*>.*\|price.*<.*" engine/screener.py
grep -n "MAX_PRICE\|MIN_PRICE\|price.*>.*\|price.*<.*" engine/stock_scorer.py

# PROBLEMA: screener filtra stocks > $25, scorer requiere stocks > $150
# RESULTADO: universo de trading vacío

# VERIFICAR fix:
# MAX_PRICE debe ser ≥ $3000 en screener
# O el scorer debe usar el mismo rango que el screener
```

#### 2.A.3 Bug notional en LIMIT orders — `strategies/strat_10_grid.py`
```python
# BUSCAR parámetro notional en órdenes:
grep -n "notional\|limit_order\|submit_order" strategies/strat_10_grid.py

# PROBLEMA: Alpaca rechaza `notional` en LIMIT orders con HTTP 400
# Solo es válido en MARKET orders

# VERIFICAR fix:
# La orden LIMIT debe calcular qty = notional / price localmente
# Luego enviar la orden con qty= en lugar de notional=
```

#### 2.A.4 Bug settled_cash = 0 en paper trading — `order_manager.py` / `order_manager_equities.py`
```python
# BUSCAR lógica de sizing:
grep -n "settled_cash\|buying_power\|is_paper\|PAPER" engine/order_manager.py
grep -n "settled_cash\|buying_power\|is_paper\|PAPER" engine/order_manager_equities.py

# PROBLEMA: En paper trading, settled_cash = 0.0, bloquea sizing dinámico
# SOLUCIÓN: Usar buying_power como fallback cuando is_paper=True
```

### 2.B — Bugs Importantes a Verificar

#### 2.B.1 SMA200 en minutos en vez de días — `strategies_equities/strat_02_vcp.py`
```python
# BUSCAR inicialización de SMA:
grep -n "SMA200\|sma_200\|TimeFrame\|Day\|Minute" strategies_equities/strat_02_vcp.py

# PROBLEMA: SMA200 se calcula con 200 barras de 1 minuto = ~3.3 horas
# Debería ser 200 barras diarias = ~10 meses de tendencia real
```

#### 2.B.2 Grid flag sin persistencia — `strategies/strat_10_grid.py`
```python
# BUSCAR flag de deploy:
grep -n "_grid_deployed\|deployed\|sqlite\|redis\|persist" strategies/strat_10_grid.py

# PROBLEMA: Flag _grid_deployed se pierde en cada reinicio del bot
# Al reiniciar, el grid se despliega nuevamente = 31+ órdenes duplicadas
```

#### 2.B.3 Telegram no envía alertas — `engine/notifier.py`
```python
# VERIFICAR configuración:
grep -n "BOT_TOKEN\|CHAT_ID\|telegram\|send_message" engine/notifier.py
cat .env | grep -i telegram
# Probar manualmente:
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
import requests
token = os.getenv('TELEGRAM_BOT_TOKEN')
chat = os.getenv('TELEGRAM_CHAT_ID')
print(f'Token: {bool(token)}, Chat: {bool(chat)}')
r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
    json={'chat_id': chat, 'text': 'Test auditoria'})
print(r.status_code, r.text)
"
```

### 2.C — Verificaciones de Hardening

#### 2.C.1 Short Firewall activo
```python
grep -n "short.*firewall\|SELL.*position\|has_position\|side.*sell" engine/order_manager_equities.py
# Debe existir validación que impida SELL si no hay posición larga abierta
```

#### 2.C.2 STATE_CACHE implementado
```python
grep -n "STATE_CACHE\|update_cache\|cached" main.py
# Debe existir cache para evitar llamadas directas a Alpaca en cada request del dashboard
```

#### 2.C.3 Rate limiting en OrderManager
```python
grep -n "sleep\|asyncio.sleep\|0\.4\|rate.limit\|delay" engine/order_manager.py
# Debe existir delay de ~0.4s entre órdenes
```

### Checkpoint FASE 2 ✅
- [ ] VIXY threshold verificado (≥25.0 ó usando ^VIX real)
- [ ] Price filter verificado (MAX_PRICE ≥ 3000)
- [ ] notional en LIMIT orders: NO existe (usa qty calculado)
- [ ] settled_cash: fallback a buying_power cuando paper=True
- [ ] Short firewall: activo en order_manager_equities.py
- [ ] STATE_CACHE: implementado en main.py
- [ ] Rate limiting: delay entre órdenes presente
- [ ] Todos los bugs anotados con archivo + número de línea exacto

> **Agentes adicionales si se encuentran bugs no documentados:** Crear
> `Agente_Code_Deep_[NombreBug]` para investigar la causa raíz del bug adicional.

---

## FASE 3 — AUDITORÍA DEL RUNTIME (Programa en Ejecución)

**Objetivo:** Observar el bot mientras opera para detectar problemas que no aparecen en el código estático.
**Agente:** `Agente_Runtime`
**Duración estimada:** 60 minutos (durante horario de mercado) ó 20 minutos (fuera de horario)
**Prerrequisito:** FASE 2 completada

### 3.1 Monitoreo de logs en tiempo real
```bash
# Abrir 3 terminales en paralelo:
# Terminal 1 - Engine principal
journalctl -u alpacanode-main -f | grep -v "DEBUG"

# Terminal 2 - Crypto
journalctl -u alpacanode-crypto -f | grep -v "DEBUG"

# Terminal 3 - Equities
journalctl -u alpacanode-equities -f | grep -v "DEBUG"
```

### 3.2 Verificar señales de compra (si es horario de mercado)
```bash
# El bot debe emitir señales. Buscar en logs:
journalctl -u alpacanode-main --since "today" | grep -E "SIGNAL|BUY|SELL|ORDER|regime|BULL|BEAR|CHOP"
journalctl -u alpacanode-equities --since "today" | grep -E "SIGNAL|BUY|SELL|screener|scored"
```

### 3.3 Verificar régimen actual
```python
# Ejecutar en VPS:
python3 -c "
import sys
sys.path.insert(0, '/app')
from dotenv import load_dotenv
load_dotenv('/app/.env')
from engine.regime_manager import RegimeManager
rm = RegimeManager()
import asyncio
regime = asyncio.run(rm.get_regime())
print(f'Régimen actual: {regime}')
print(f'VIX value: {rm._last_vix}')
"
```

### 3.4 Verificar posiciones activas
```bash
curl -s http://localhost:8000/api/positions | python3 -m json.tool
curl -s http://localhost:8000/api/account | python3 -m json.tool
curl -s http://localhost:8000/api/orders | python3 -m json.tool
```

### 3.5 Verificar WebSocket activo
```bash
# Buscar en logs la confirmación de conexión WebSocket:
journalctl -u alpacanode-main --since "today" | grep -E "WebSocket|connected|subscribed|streaming"
# No deben aparecer: "disconnected", "reconnecting" en loop
```

### 3.6 Stress test del dashboard
```bash
# 10 requests en 5 segundos — no debe causar 429
for i in {1..10}; do
  curl -s -o /dev/null -w "%{http_code} " http://localhost:8000/api/account
done
echo ""
```

### Checkpoint FASE 3 ✅
- [ ] Los 3 engines están generando logs activos (no silenciosos)
- [ ] El régimen se detecta correctamente (BULL/BEAR/CHOP según mercado real)
- [ ] VIX value es un número razonable (entre 10 y 80)
- [ ] WebSocket conectado y no en loop de reconexión
- [ ] Dashboard responde en < 200ms sin errores 429
- [ ] Durante mercado abierto: al menos 1 señal de compra visible en logs en 60 min

> **Si el bot no genera señales después de 60 min de mercado abierto:**
> Crear `Agente_Signal_Debug` para trazar el flujo de datos desde WebSocket hasta estrategia.

---

## FASE 4 — CORRECCIONES (Por Prioridad)

**Objetivo:** Aplicar las correcciones identificadas en Fases 2 y 3, en orden de prioridad.
**Agentes:** Uno por cada corrección (ver sub-sección)
**Prerrequisito:** FASE 3 completada. Hacer `git commit` del baseline antes de cada cambio.

### Reglas de Corrección
1. **Siempre hacer commit** antes de aplicar un fix (`git add -p && git commit -m "pre-fix-backup"`)
2. **Un fix por commit** — nunca mezclar correcciones
3. **Probar tras cada fix** antes de pasar al siguiente
4. **Documentar** el cambio exacto con archivo + línea modificada

---

### FIX-01 · VIXY vs VIX (CRÍTICO)
**Agente:** `Agente_Fix_VIXY`
**Archivo:** `engine/regime_manager.py`

**Opción rápida (5 min):**
```python
# Cambiar umbral de 30 a 50 para VIXY
VIX_BULL_THRESHOLD = 50.0    # VIXY rango normal $25-$40
VIX_BEAR_THRESHOLD = 65.0    # VIXY en pánico real > $50
```

**Opción robusta (30 min):**
```python
# Usar yfinance para leer ^VIX real
import yfinance as yf
vix = yf.download("^VIX", period="1d", interval="1d")
vix_value = float(vix['Close'].iloc[-1])
# Umbral: BULL si VIX < 20, BEAR si VIX > 25
VIX_BULL_THRESHOLD = 20.0
VIX_BEAR_THRESHOLD = 25.0
```

**Verificación post-fix:**
```python
python3 -c "
from engine.regime_manager import RegimeManager
import asyncio
rm = RegimeManager()
regime = asyncio.run(rm.get_regime())
print(f'Régimen: {regime}')
# Esperado: BULL si mercado tranquilo, no siempre BEAR
"
```

---

### FIX-02 · Price Filter Contradiction (CRÍTICO)
**Agente:** `Agente_Fix_Prices`
**Archivos:** `engine/screener.py`, `engine/stock_scorer.py`

```python
# En screener.py — aumentar MAX_PRICE
MAX_PRICE = 3000.0    # Era: 25.0

# En stock_scorer.py — alinear con screener
MIN_SCORE_PRICE = 10.0    # Era: 150.0 (o eliminar filtro de precio en scorer)
```

**Verificación post-fix:**
```bash
python3 -c "
from engine.screener import Screener
s = Screener()
import asyncio
universe = asyncio.run(s.run())
print(f'Stocks en universo: {len(universe)}')
print(universe[:5])
# Esperado: lista no vacía de tickers
"
```

---

### FIX-03 · notional en LIMIT orders (CRÍTICO)
**Agente:** `Agente_Fix_Notional`
**Archivo:** `strategies/strat_10_grid.py`

```python
# ANTES (genera HTTP 400):
order = api.submit_order(
    symbol=symbol,
    notional=trade_amount,
    side='buy',
    type='limit',
    limit_price=price
)

# DESPUÉS (correcto):
qty = round(trade_amount / price, 6)    # Calcular qty localmente
order = api.submit_order(
    symbol=symbol,
    qty=qty,
    side='buy',
    type='limit',
    limit_price=price
)
```

**Verificación post-fix:**
```bash
# Simular una orden en paper trading y verificar que no hay HTTP 400
journalctl -u alpacanode-main --since "2 minutes ago" | grep -E "400|notional|grid.*order"
```

---

### FIX-04 · settled_cash = 0 en paper trading (CRÍTICO)
**Agente:** `Agente_Fix_Cash`
**Archivos:** `engine/order_manager.py`, `engine/order_manager_equities.py`

```python
# ANTES:
cash = float(account.settled_cash)
if cash < MIN_TRADE:
    return None  # Bloquea todas las órdenes en paper trading

# DESPUÉS:
if os.getenv('PAPER_TRADING', 'false').lower() == 'true':
    cash = float(account.buying_power)
else:
    cash = float(account.settled_cash)
if cash < MIN_TRADE:
    return None
```

**Verificación post-fix:**
```python
python3 -c "
from engine.order_manager import OrderManager
om = OrderManager()
import asyncio
size = asyncio.run(om._calculate_position_size('SPY', 450.0))
print(f'Tamaño de posición calculado: {size}')
# Esperado: número > 0 (no None)
"
```

---

### FIX-05 · SMA200 en minutos (ALTO)
**Agente:** `Agente_Fix_SMA200`
**Archivo:** `strategies_equities/strat_02_vcp.py`

```python
# En __init__ o método de inicialización, añadir:
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

def _init_daily_sma200(self, symbol: str):
    client = StockHistoricalDataClient(api_key, secret_key)
    end = datetime.now()
    start = end - timedelta(days=300)  # Suficiente para 200 días hábiles
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end
    )
    bars = client.get_stock_bars(request)
    closes = [b.close for b in bars[symbol]]
    if len(closes) >= 200:
        self.sma200_daily = sum(closes[-200:]) / 200
    else:
        self.sma200_daily = None  # No hay suficientes datos
```

---

### FIX-06 · Grid Flag Persistence (ALTO)
**Agente:** `Agente_Fix_Grid`
**Archivo:** `strategies/strat_10_grid.py`

```python
# Usar SQLite para persistir el flag
import sqlite3
import os

DB_PATH = os.getenv('DB_PATH', '/app/data/trades.db')

def _is_grid_deployed(self, symbol: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM grid_state WHERE symbol=? AND deployed=1", (symbol,)
        ).fetchone()
    return row is not None

def _set_grid_deployed(self, symbol: str, deployed: bool):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO grid_state (symbol, deployed) VALUES (?, ?)",
            (symbol, int(deployed))
        )
        conn.commit()
```

---

### FIX-07 · Telegram Notifier (MEDIO)
**Agente:** `Agente_Fix_Telegram`
**Archivo:** `engine/notifier.py`

```bash
# Paso 1: Verificar variables en .env
grep -E "TELEGRAM" /app/.env

# Paso 2: Si faltan, añadir:
echo "TELEGRAM_BOT_TOKEN=tu_token_aqui" >> /app/.env
echo "TELEGRAM_CHAT_ID=tu_chat_id_aqui" >> /app/.env

# Paso 3: Obtener chat_id si no se conoce
curl "https://api.telegram.org/bot{TOKEN}/getUpdates"
```

### Checkpoint FASE 4 ✅
- [ ] FIX-01: Régimen detecta BULL en mercado tranquilo (VIX < 20)
- [ ] FIX-02: Screener devuelve ≥ 5 stocks en universo
- [ ] FIX-03: Grid no genera errores HTTP 400
- [ ] FIX-04: Position sizing calcula qty > 0 en paper trading
- [ ] FIX-05: SMA200 usa barras diarias (verificar en logs)
- [ ] FIX-06: Grid no crea órdenes duplicadas al reiniciar
- [ ] FIX-07: Telegram envía mensaje de prueba correctamente
- [ ] Todos los fixes commiteados individualmente en git

---

## FASE 5 — VALIDACIÓN END-TO-END

**Objetivo:** Verificar que el sistema completo funciona después de las correcciones.
**Agente:** `Agente_Validador`
**Duración estimada:** 2 horas (incluye sesión de mercado)
**Prerrequisito:** FASE 4 completada

### 5.1 Reinicio limpio del sistema
```bash
# Detener todos los servicios
systemctl stop alpacanode-main alpacanode-crypto alpacanode-equities

# Verificar que no quedan procesos
ps aux | grep python | grep -v grep

# Reiniciar uno a uno con espera de 30 segundos entre cada uno
systemctl start alpacanode-main && sleep 30
systemctl status alpacanode-main | grep Active
systemctl start alpacanode-crypto && sleep 30
systemctl start alpacanode-equities
```

### 5.2 Test de flujo completo (durante mercado abierto)
```bash
# Monitorear durante 30 minutos buscando:
# 1. Conexión WebSocket exitosa
# 2. Pre-fetch histórico completado
# 3. Régimen calculado (no BEAR con VIX bajo)
# 4. Al menos 1 screener run completado (equities)
# 5. Al menos 1 señal de compra generada
# 6. Al menos 1 orden enviada a Alpaca sin error

journalctl -u alpacanode-main --since "now" -f | tee /tmp/validation_log.txt
```

### 5.3 Verificación de dashboard
```bash
# Todas estas URLs deben responder con datos reales (no vacíos)
curl -s http://localhost:8000/api/account   | python3 -m json.tool | grep "buying_power"
curl -s http://localhost:8000/api/positions | python3 -m json.tool
curl -s http://localhost:8000/api/orders    | python3 -m json.tool | head -30
```

### 5.4 Test de reinicio (simular crash)
```bash
# Simular fallo y verificar que el bot no genera órdenes duplicadas
systemctl stop alpacanode-main
sleep 10
systemctl start alpacanode-main
sleep 30
# Verificar en dashboard: órdenes abiertas son las mismas que antes del reinicio
curl -s http://localhost:8000/api/orders | python3 -c "
import sys, json
orders = json.load(sys.stdin)
print(f'Órdenes abiertas: {len(orders)}')
"
```

### 5.5 Test de Telegram
```bash
# Forzar una notificación de prueba
python3 -c "
import sys, asyncio
sys.path.insert(0, '/app')
from dotenv import load_dotenv; load_dotenv('/app/.env')
from engine.notifier import Notifier
n = Notifier()
asyncio.run(n.send('✅ Test validacion FASE 5 completada'))
"
```

### Checkpoint FASE 5 ✅
- [ ] Bot arranca sin errores en < 2 minutos
- [ ] Régimen detectado es BULL o CHOP (no siempre BEAR)
- [ ] Durante mercado abierto: ≥ 1 señal de compra en 30 min
- [ ] Durante mercado abierto: ≥ 1 orden enviada sin HTTP 400/422
- [ ] Reinicio no genera órdenes duplicadas
- [ ] Dashboard muestra datos en tiempo real (no vacío)
- [ ] Telegram recibe mensaje de prueba
- [ ] 0 errores CRITICAL en logs durante la sesión de validación

> **Si algún checkpoint falla:** El agente debe crear un `Agente_Debug_[Componente]`
> específico para investigar el problema antes de cerrar esta fase.

---

## FASE 6 — MONITOREO POST-CORRECCIÓN

**Objetivo:** Monitoreo continuo para detectar regresiones o nuevos problemas.
**Agente:** `Agente_Monitor`
**Duración:** Primeras 72 horas post-corrección, luego revisión semanal
**Prerrequisito:** FASE 5 completada con todos los checkpoints en verde

### 6.1 Script de health check automático
```bash
# Crear script en VPS: /app/health_check.sh
#!/bin/bash
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== Health Check $TIMESTAMP ===" >> /app/data/health.log

# Servicios activos
for svc in alpacanode-main alpacanode-crypto alpacanode-equities; do
  STATUS=$(systemctl is-active $svc)
  echo "  $svc: $STATUS" >> /app/data/health.log
done

# Errores en última hora
ERRORS=$(journalctl -u alpacanode-main --since "1 hour ago" | grep -c "CRITICAL\|ERROR 4[0-9][0-9]")
echo "  Errores última hora: $ERRORS" >> /app/data/health.log

# Dashboard responde
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/account)
echo "  Dashboard: HTTP $HTTP" >> /app/data/health.log

# Alertar por Telegram si hay problemas
if [ "$ERRORS" -gt 10 ] || [ "$HTTP" != "200" ]; then
  curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
    -d "chat_id=$TELEGRAM_CHAT_ID&text=⚠️ AlpacaNode ALERTA: $ERRORS errores, Dashboard HTTP $HTTP"
fi
```

```bash
# Programar cada 15 minutos durante mercado
crontab -e
# Añadir:
*/15 9-16 * * 1-5 /app/health_check.sh
```

### 6.2 Métricas a monitorear diariamente

| Métrica | Objetivo | Alerta si |
|---------|----------|-----------|
| Señales por día | ≥ 3 señales | 0 señales en día de mercado |
| Órdenes ejecutadas | ≥ 1 por semana | 0 órdenes en 5 días hábiles |
| Errores HTTP 400/422 | 0 | > 0 errores en 1 hora |
| Errores HTTP 429 | 0 | > 5 en 1 hora |
| Servicios activos | 3 de 3 | Cualquier servicio caído |
| Tiempo de uptime | > 99% | Reinicio no programado |

### 6.3 Revisión semanal (manual)
```bash
# Cada lunes antes de apertura de mercado:
# 1. Revisar P&L semanal
curl -s http://localhost:8000/api/account | python3 -m json.tool | grep "equity\|cash\|pnl"

# 2. Revisar órdenes de la semana
journalctl -u alpacanode-main --since "7 days ago" | grep "ORDER FILLED" | wc -l

# 3. Verificar que no hay acumulación de errores
journalctl -u alpacanode-main --since "7 days ago" | grep "CRITICAL" | wc -l
```

### Checkpoint FASE 6 (72h post-corrección) ✅
- [ ] 0 reinicios no programados de servicios
- [ ] Health check automatizado corriendo cada 15 min
- [ ] P&L no negativo (bot no está perdiendo por bugs)
- [ ] Al menos 1 orden completada en la semana
- [ ] 0 errores CRITICAL acumulados
- [ ] Telegram enviando alertas del health check

---

## INVENTARIO DE AGENTES

| ID | Nombre | Fase | Propósito | Prioridad |
|----|--------|------|-----------|-----------|
| A00 | Agente_Baseline | 0 | Captura estado inicial del sistema | Prerequisito |
| A01 | Agente_VPS | 1 | Audita infraestructura, servicios, red | Alta |
| A02 | Agente_Code | 2 | Revisa código fuente, identifica bugs | Alta |
| A03 | Agente_Runtime | 3 | Observa bot en ejecución en vivo | Alta |
| A04 | Agente_Fix_VIXY | 4 | Corrige umbral VIX/VIXY | CRÍTICA |
| A05 | Agente_Fix_Prices | 4 | Corrige filtros de precio contradictorios | CRÍTICA |
| A06 | Agente_Fix_Notional | 4 | Corrige parámetro notional en LIMIT orders | CRÍTICA |
| A07 | Agente_Fix_Cash | 4 | Corrige settled_cash=0 en paper trading | CRÍTICA |
| A08 | Agente_Fix_SMA200 | 4 | Corrige SMA200 a barras diarias | Alta |
| A09 | Agente_Fix_Grid | 4 | Persiste flag de grid en SQLite | Alta |
| A10 | Agente_Fix_Telegram | 4 | Configura y verifica notificaciones | Media |
| A11 | Agente_Validador | 5 | Prueba end-to-end post-correcciones | Alta |
| A12 | Agente_Monitor | 6 | Monitoreo continuo 72h post-fix | Media |

### Agentes Adicionales (Bajo Demanda)
Los siguientes agentes se crean **solo si la auditoría revela problemas no documentados**:

| Trigger | Agente a Crear | Propósito |
|---------|----------------|-----------|
| 0 señales en 60 min de mercado | `Agente_Signal_Debug` | Trazar flujo WebSocket → Estrategia |
| Servicios zombie en FASE 0 | `Agente_VPS_Cleanup` | Limpiar procesos y reiniciar |
| Error de red persistente | `Agente_VPS_Network` | Diagnosticar conectividad |
| Bug no documentado en FASE 2 | `Agente_Code_Deep_[Bug]` | Investigar causa raíz |
| Crash en FASE 5 | `Agente_Debug_[Componente]` | Investigar el componente que falla |
| P&L negativo post-fix | `Agente_PnL_Analysis` | Analizar trades perdedores |

---

## MATRIZ DE RIESGO

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| Fix rompe algo que funcionaba | Media | Alto | Commit antes de cada fix, rollback disponible |
| VPS sin espacio en disco | Baja | Crítico | Verificar en FASE 1, limpiar logs si < 5GB |
| Alpaca cambia su API | Baja | Alto | Monitorear changelog en alpaca.markets/docs |
| Paper trading no refleja live | Alta | Medio | Probar con orders mínimas en live antes de escalar |
| Mercado cerrado durante validación | Alta | Bajo | Programar FASE 5 para horario de mercado EST |

---

## CRITERIOS DE ÉXITO FINAL

El bot está **completamente funcional** cuando se cumplan TODOS estos criterios:

### Funcional
- [ ] El bot genera ≥ 1 señal de compra válida por día de mercado
- [ ] Al menos 1 orden se ejecuta exitosamente en la semana
- [ ] El régimen se detecta correctamente (BULL cuando VIX < 20)
- [ ] El universo de equities tiene ≥ 5 stocks en el screener

### Técnico
- [ ] 0 errores HTTP 400/422 en órdenes
- [ ] 0 errores HTTP 429 (rate limiting) en 24 horas
- [ ] Dashboard responde en < 500ms
- [ ] Reinicio no genera órdenes duplicadas

### Operativo
- [ ] Telegram envía alertas de trades
- [ ] Health check automático corriendo
- [ ] Todos los fixes en git con mensajes descriptivos
- [ ] Documentación actualizada con los cambios realizados

---

## REFERENCIAS

- **Código fuente:** `/app/` (VPS) ó `c:\Users\user\OneDrive\Escritorio\gemini cli\trader\` (local)
- **Contexto auditoria:** Ver archivos en `/AUDITORIA/`
- **Alpaca API docs:** https://docs.alpaca.markets/
- **Dashboard:** http://[VPS_IP]:8000
- **Logs en tiempo real:** `journalctl -u alpacanode-main -f`

---

*Documento generado el 2026-04-15. Actualizar con hallazgos de cada fase.*
*Próxima revisión planificada: 2026-04-22 (72h post-correcciones).*
