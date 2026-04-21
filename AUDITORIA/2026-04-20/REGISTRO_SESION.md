# REGISTRO DE SESIÓN — 2026-04-20
**Motor:** AlpacaNode Trading Engine  
**VPS:** 148.230.82.14 | PID activo: 235995  
**Modo:** PAPER TRADING (PKZD*** key)

---

## Cambios aplicados en esta sesión

### 1. Visibilidad del cap nocturno de crypto
**Archivo:** `engine/order_manager_crypto.py`  
**Commit:** `01758b5`  
**Cambio:** `logger.debug` → `logger.info` en `_get_dynamic_cap()`  
**Por qué:** Los logs nocturnos de crypto no aparecían en el dashboard porque `debug`
está filtrado. Ahora se puede confirmar cuánto capital usa crypto durante la noche.

---

### 2. Dashboard: órdenes BUY mostraban qty=0
**Archivos:** `api_server.py` + `static/index.html`  
**Commit:** `dfea1e6` (Agent A del equipo de agentes)  
**Cambio en api_server.py:** Se añadieron dos campos nuevos al dict `ord_data`:
```python
"notional":        float(o.notional) if o.notional else None,
"filled_notional": float(getattr(o, 'filled_notional', None) or 0) or None,
```
**Cambio en index.html línea ~1170:** La celda QTY ahora muestra el monto en dólares
si qty es 0 (órdenes notional):
```javascript
// Antes:
<td>${o.qty}</td>
// Después:
<td>${o.qty > 0 ? o.qty : (o.notional ? '$'+o.notional.toFixed(2) : ...)}</td>
```
**Por qué:** Las órdenes ETF se crean con `notional=` en dólares. Alpaca devuelve
`qty=0` hasta que se ejecutan parcialmente. El dashboard mostraba "BUY 0" que confundía.

---

### 3. Logs del dashboard vacíos (logrotate)
**Archivo en VPS:** `/etc/logrotate.d/alpacatrader`  
**Acción:** (Agent B del equipo de agentes)  
**Cambio:** Añadida directiva `copytruncate` a la config de logrotate.  
**Por qué:** logrotate movía el archivo `engine.log` a medianoche pero Python mantenía
el file descriptor apuntando al archivo viejo (ahora `engine.log.1`). El nuevo
`engine.log` quedaba vacío. Con `copytruncate`, logrotate copia el contenido y trunca
el original en lugar de moverlo — Python sigue escribiendo al mismo fd.

---

### 4. Reinicio diario automático a las 4am ET (memoria)
**Archivo en VPS:** `/etc/cron.d/alpacatrader-restart` (nuevo)  
**Acción:** (Agent B del equipo de agentes)  
**Contenido creado:**
```
0 8 * * * root systemctl restart alpacatrader
```
(8 UTC = 4am ET en EDT verano)  
**Por qué:** El proceso acumulaba 1.19 GB RAM después de 6 días sin reiniciar.
Sin swap en el VPS, el OOM killer podía matarlo silenciosamente.
El reinicio a las 4am no interrumpe trading (mercado abre 9:30am ET, crypto
está en baja actividad). Además, al reiniciar se ejecuta `_adopt_orphan_positions()`
automáticamente.

---

### 5. Fix _adopt_orphan_positions: detectar SHORTs en ETF whitelist
**Archivo:** `main_equities.py`  
**Commit:** `a0e9552`  
**Cambio:** Añadido bloque ANTES del filtro `eq_positions` que cierra cualquier
SHORT en la `etf_whitelist`:
```python
for p in positions:
    qty = float(p.qty)
    if p.symbol in etf_whitelist and qty < 0:
        client.close_position(p.symbol)
        logger.warning(f"[EquitiesEngine] SHORT huérfano en ETF cerrado: {p.symbol} qty={qty}")
```
**Por qué:** XLK tenía -50 shares (SHORT) desde el 13 de abril cuando se deshabilitó
Pairs Trading. La función `_adopt_orphan_positions()` no lo detectaba porque XLK
está en la `etf_whitelist` (que filtra ETFs para no ponerles trailing stops) y
además el guard `qty <= 0` saltaba los shorts. Se perdían ~$569/día en interés
de short mientras la posición existía.

---

### 6. Reactivación de Pairs Trading (strat_09)
**Archivos:** `strategies/strat_09_pairs.py` + `strategies/__init__.py` + `main.py`  
**Commits:** `f775917` + `c79ed9d`

**Problema original:** Pairs Trading fue deshabilitado porque abría shorts huérfanos
al reiniciar (perdía el estado `_position_type`).

**Fixes aplicados:**
- `_restore_state()`: al arrancar, consulta Alpaca para saber si ya existe la posición
  y restaura `_position_type` correctamente.
- `_has_real_position(symbol)`: verifica que la posición realmente existe en Alpaca
  antes de intentar cerrarla (evita error 40410000 "position not found").
- `QTY` reducido de 10 a 5 para menor exposición.
- Usa `order_manager.buy()` y `order_manager.sell()` sin qty (compatible con el
  notional sizing del OrderManager ETF).

**En `strategies/__init__.py`:** Se descomentó la importación de `PairsTradingStrategy`.  
**En `main.py`:** Se añadió `PairsTradingStrategy` al import y a `_register_strategies()`.
El log cambió de "8 estrategias ETF registradas" a "9 estrategias ETF registradas".

---

### 7. Trailing stops para posiciones equities huérfanas (LCID, SPCE)
**Acción:** Colocación manual de trailing stops GTC al 15% para posiciones sin gestión.  
**Fix técnico:** Alpaca no acepta trailing stops GTC con qty fraccionaria. Se usó
`math.floor(qty)` para obtener entero antes de enviar la orden.

---

## Estado del sistema al cierre de sesión

| Motor | Estado | Estrategias activas |
|-------|--------|---------------------|
| ETF | ✅ Running | 9 registradas (régimen filtra a 3-6 según BEAR/CHOP/BULL) |
| Crypto | ✅ Running | 10 registradas (caps dinámicos: $15 día, hasta $40 noche) |
| Equities | ✅ Running | 6 registradas (filtradas por régimen + portfolio_manager) |

**Régimen actual estimado:** BEAR (SPY bajo SMA200, VIX elevado por tariff shock)  
**Problema pendiente:** Con BEAR, solo 3 ETF estrategias activas → ver plan en
`PLAN_OPCION_A_regime_sizing.md` en esta misma carpeta.

---

## Posición cerrada manualmente

| Símbolo | Tipo | Qty | Motivo |
|---------|------|-----|--------|
| XLK | SHORT | -50 shares | Short huérfano de Pairs Trading del 13-abril. Drenaba ~$569/día. Cerrado manualmente. |

---

## Bugs identificados pero NO corregidos todavía

1. **VWAP (strat_08_vwap.py líneas 101 y 114):** Llama a `order_manager.buy(symbol, qty=20, ...)`
   pero la firma del OrderManager ETF no acepta `qty`. Causa TypeError silencioso.
   Por eso estaba comentada en `__init__.py`. Fix detallado en el plan Opción A.

2. **Pairs Trading solo activa en CHOP:** Con el régimen actual BEAR, Pairs Trading
   (recién reactivada) está inmediatamente bloqueada por `is_strategy_enabled(9)`.

3. **Momentum Rotation y Sector Rotation:** Solo ejecutan los viernes a las 15:30 ET.
   Son normalmente inactivas 6 días de 7.
