# Resumen de Sesión — 2026-04-17

## Contexto de la Sesión
Continuación del refactor de eliminación del Daily Mode A/B/C (completado en sesión anterior, commit `6bd6064`).
Esta sesión se enfocó en tres bloques: verificación del deploy anterior, corrección de bugs encontrados en producción, y nuevas mejoras de seguridad para micro-cuenta de $100 USD.

---

## Bloque 1 — Verificación del Deploy Anterior (Daily Mode Refactor)

### Problema encontrado: `subscribe_news` en `StockDataStream`
- **Commit:** `9bb63c9`
- **Archivo:** `main.py` (línea 192)
- **Error:** `CRITICAL: 'StockDataStream' object has no attribute 'subscribe_news'`
- **Causa:** El `StockDataStream` de Alpaca no soporta suscripción de noticias. La llamada fue añadida en commit anterior `4c5bcde` pero siempre estuvo rota (se logueaba como CRITICAL pero el bot seguía vivo por el try/except).
- **Fix:** Eliminadas las 3 líneas de `subscribe_news` de `_subscribe()`. El `_on_news` y `dispatch_news` se mantienen para uso futuro con `NewsDataStream` real.

---

## Bloque 2 — Fixes de Dashboard y Circuit Breaker (commit `9bb63c9`)

### Fix A — Columna ESTRATEGIA en Posiciones Equities
- **Archivos modificados:** `api_server.py`, `static/index.html`
- **Problema:** La columna ESTRATEGIA en la tabla de posiciones equities del dashboard siempre mostraba "—". Alpaca no asocia estrategia a posiciones — solo existe en el `client_order_id` de las órdenes.
- **Solución:** En `api_server.py` (líneas ~322-356), antes de procesar posiciones, se construye un dict `symbol_to_strategy` leyendo las últimas 100 órdenes filled con prefijo `eq_`. Se usa `parse_order_meta(client_order_id)` para extraer el nombre. Al categorizar posiciones equities, se agrega el campo `strategy` al `pos_data`.
- **En `index.html`:** Línea 1397 cambiada de `—` hardcodeado a `${p.strategy || '—'}`.

### Fix B — Auto-Resume del Circuit Breaker (primera versión: timer 24h)
- **Archivo:** `engine/portfolio_manager.py`
- **Cambios:**
  - `__init__`: agrega `self._halted_at = None`
  - `_trigger_halt()`: guarda `self._halted_at = datetime.now()`
  - `resume()`: limpia `self._halted_at = None` y `halt_reason = None`
  - `check()`: verifica si han pasado ≥24h desde el halt → llama `resume()` automáticamente
- **Nota:** Esta versión fue reemplazada en Bloque 3 por lógica basada en régimen.

---

## Bloque 3 — Seguridad para Micro-Cuenta y Posiciones Huérfanas (commit `62e9a51`)

### Análisis realizado
- **Circuit breaker scope:** Solo afecta el motor Equities. ETF y Crypto siguen operando aunque el circuit breaker esté activo — esto explicaba por qué BTC seguía comprando tras la activación del cortafuegos.
- **Posiciones huérfanas:** Al reiniciar, `GammaSqueeze.sync_position_from_alpaca()` solo llama el método cuando detecta una nueva señal de compra (anti-duplicado). Si no hay señal nueva, la posición queda sin gestión indefinidamente. Posiciones de fallback (orden simple sin bracket) no tienen stop/take profit automático en Alpaca → se quedan abiertas para siempre.

### Fix 1 — Adopción de Posiciones Huérfanas
- **Archivo:** `main_equities.py`
- **Método añadido:** `_adopt_orphan_positions()` — llamado al inicio de `start_engine()`
- **Lógica:**
  1. `get_all_positions()` → filtra posiciones equities (excluye crypto y ETF whitelist)
  2. `get_orders(status=OPEN)` → construye set de símbolos ya protegidos (con bracket/stop activo)
  3. Para cada posición sin protección:
     - Precio < $1.00 → `MarketOrderRequest` SELL inmediato (liquidación de penny stocks)
     - Precio ≥ $1.00 → `TrailingStopOrderRequest(trail_percent=15.0, side=SELL)`
  4. Telegram con resumen: "Adoptadas: X, Liquidadas: Y"
- **Parámetro elegido:** `trail_percent=15%` en lugar de dólares fijos porque los activos van de $0.80 (WKHS) a $25 (GME) — un valor fijo de $2 generaría stops en negativo para penny stocks.

### Fix 2 — Circuit Breaker con Resume Inteligente por Régimen
- **Archivo:** `engine/portfolio_manager.py`
- **Archivo:** `main_equities.py` (pasa `regime_manager` al constructor)
- **Cambios en `PortfolioManager.__init__`:**
  - Nuevo parámetro: `regime_manager=None`
  - Nueva constante: `MIN_HALT_SECS_BEFORE_RESUME = 3600` (mínimo 1h antes de evaluar resume)
  - Nueva constante: `ALERT_HALT_SECS = 172800` (alerta Telegram a las 48h)
  - Nuevo flag: `self._48h_alert_sent = False`
- **Lógica de auto-resume en `check()`** (reemplaza el timer ciego de 24h):
  - Si llevan ≥1h en halt → llama `regime_manager.assess_if_needed()`
  - Si régimen es `BULL` o `CHOP` → llama `resume()` + Telegram "🟢 Auto-resume"
  - Si régimen es `BEAR` → mantiene halt, log informativo
  - Si llevan ≥48h en halt → Telegram "⚠️ 48h en halt, revisión manual recomendada" (una sola vez)
- **En `main_equities.py`:** `PortfolioManager(regime_manager=self.regime_manager)` para pasarle el contexto de mercado.

---

## Archivos Modificados en esta Sesión

| Archivo | Commits | Descripción |
|---------|---------|-------------|
| `main.py` | `9bb63c9` | Eliminado `subscribe_news` roto de `_subscribe()` |
| `api_server.py` | `9bb63c9` | Symbol→strategy map para posiciones equities |
| `static/index.html` | `9bb63c9` | Columna ESTRATEGIA lee `p.strategy` de la API |
| `engine/portfolio_manager.py` | `9bb63c9`, `62e9a51` | Auto-resume 24h → resume inteligente por régimen |
| `main_equities.py` | `62e9a51` | `_adopt_orphan_positions()` + pasar `regime_manager` |

---

## Bloque 4 — Capital Dinámico para Crypto en Horario Off-Market (commit `89f1e2e`)

### Motivación
Cuando ETF y Equities están inactivos (mercado cerrado 16:30–9:30 ET, fines de semana), el capital permanece completamente ocioso. Crypto opera 24/7, por lo que tiene sentido liberar más capital en esas horas sin comprometer la apertura del día siguiente.

### Fix — Capital Nocturno en `order_manager_crypto.py`
- **Archivo:** `engine/order_manager_crypto.py`
- **Función nueva:** `_us_market_is_open()` — detecta horario de mercado por día de semana + hora (no llama a la API de Alpaca, usa `datetime.now()` con TZ America/New_York ya configurado en `main.py`)
- **Método nuevo:** `_get_dynamic_cap()` — retorna el cap en USD según horario:
  - **Mercado abierto** → `$15` fijo (conservador, ETF/Equities activos)
  - **Mercado cerrado** → calcula dinámicamente:
    - Lee `settled_cash` y `equity` de Alpaca
    - Reserva 40% del settled_cash para la apertura
    - Del 60% disponible, permite hasta 20% por posición, tope absoluto `$40`
    - Si `equity < $80` → fuerza cap de $15 (protección de micro-cuenta)
- **Constantes añadidas:**
  ```python
  DAY_CAP_USD       = 15.0   # Cap horario de mercado
  NIGHT_CAP_MAX_USD = 40.0   # Tope absoluto nocturno
  NIGHT_CAP_PCT     = 0.20   # % del disponible nocturno por posición
  NIGHT_RESERVE_PCT = 0.40   # % de settled_cash reservado para apertura
  MIN_EQUITY_EXPAND = 80.0   # Mínimo equity para activar modo noche
  ```
- **Integración:** `_calculate_crypto_qty()` ahora llama `_get_dynamic_cap()` en lugar del `min(notional, 15.0)` hardcodeado.

### Comportamiento con cuenta de $100

| Momento | Equity | settled_cash | Reserva | Disponible | Cap por posición |
|---------|--------|-------------|---------|-----------|-----------------|
| Día (mercado abierto) | cualquiera | — | — | — | $15 fijo |
| Noche (equity ≥ $80) | $100 | $60 | $24 | $36 | $7.20 (20% de $36) |
| Noche (equity $125+) | $125 | $75 | $30 | $45 | $15 → empieza a superar el cap diurno |
| Noche (equity $200) | $200 | $120 | $48 | $72 | $14.40 → crece con la cuenta |
| Noche (equity $300) | $300 | $180 | $72 | $108 | $21.60 → supera cap diurno |

El cap nocturno supera el diurno de $15 cuando la cuenta crece por encima de ~$125, creando crecimiento proporcional y automático.

---

## Commits de esta Sesión

| Hash | Mensaje |
|------|---------|
| `9bb63c9` | fix: circuit breaker auto-resume 24h, estrategia en posiciones, subscribe_news cleanup |
| `62e9a51` | fix: adopción de posiciones huérfanas + circuit breaker inteligente por régimen |
| `89f1e2e` | feat: capital dinámico para crypto en horario off-market |

---

## Estado del Bot al Cierre de Sesión

- **Servicio VPS:** `active` (commit `89f1e2e`, `systemctl is-active alpacatrader`)
- **Paper Trading:** Sí (`PKZD***`)
- **3 motores activos:** ETF (8 estrategias), Crypto (10 estrategias), Equities (6 estrategias)
- **Sin errores CRITICAL** en logs de arranque
- **Posiciones huérfanas:** Se gestionarán automáticamente en el próximo reinicio
- **Capital crypto:** Modo noche activo (mercado cerrado actualmente)

---

## Pendiente / Riesgos Residuales

- **No hay cortafuegos global entre los 3 motores.** Si el equity total cae, solo Equities para. ETF y Crypto siguen. Pendiente implementar un monitor global de cuenta.
- **XLK qty=-50** (posición corta) visible en logs de SectorRotation — revisar si es una posición real o error de sincronización en Paper Trading.
- **`subscribe_news` deshabilitado** — las estrategias NLP y PEAD no reciben noticias en tiempo real vía WebSocket. Funcionan vía REST (NewsRiskFilter). Para activar el stream de noticias se necesita un `NewsDataStream` separado.
- **Crecimiento de posición sizing aún pendiente** — los caps de ETF (`MAX_POSITION_USD=100`) y Equities (`MAX_POSITION_USD=20`) siguen siendo fijos. Pendiente convertirlos a porcentaje del equity para escalar con la cuenta.
