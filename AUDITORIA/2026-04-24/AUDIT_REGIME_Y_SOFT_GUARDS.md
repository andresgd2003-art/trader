# Auditoría de Arquitectura: Regime Intradiario y Soft Guards
**Fecha:** 24 de Abril de 2026
**Autor:** Antigravity (IA)

## 1. Transición a Detección Intradiaria de Mercado (5 Minutos)

### Contexto y Problema Inicial
El bot dependía de un filtro macroeconómico diario (Media Móvil de 200 y 50 días para el SPY). Si ocurría un desplome del mercado a las 10:00 AM, el bot seguía operando bajo el supuesto de que el mercado era alcista hasta que el día terminara, dejándolo muy expuesto a volatilidad intradiaria.

### Modificaciones y Archivos
**Archivo:** `engine/regime_manager.py`

**Lógica Implementada:**
- Se cambió el parámetro de Alpaca de `TimeFrame.Day` a `TimeFrame(5, TimeFrameUnit.Minute)`.
- Se ajustaron los límites de retroceso a 10 días para asegurar suficientes barras de 5 minutos para el cálculo de medias lentas.
- Se cambió la solicitud a Yahoo Finance (`^VIX`) para usar `interval="5m"`.
- Se redujo el temporizador global `assess_if_needed()` de 1 hora (3600 segundos) a **5 minutos (300 segundos)**.
- Se actualizaron los umbrales del VIX para ser más sensibles a "spikes" intradiarios (BEAR > 22, BULL < 18).

**Resultado:** El bot actúa como un Day Trader genuino. Cruza las SMA de 20 y 50 periodos en barras de 5 minutos y recalcula el régimen (BULL/BEAR/CHOP) doce veces por hora, permitiéndole desactivar estrategias alcistas a los pocos minutos de iniciado un crash.

---

## 2. Implementación de Soft Guards (Sincronización de Posiciones)

### Contexto y Problema Inicial
Se detectó una falla arquitectónica grave inducida por los chequeos de régimen anteriores:
```python
if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER):
    return # HARD GUARD
```
Al colocar este código al principio de la función `on_bar`, la estrategia entera se congelaba (abortaba el loop) cuando el régimen le era desfavorable. 
**Consecuencia crítica:** Si una estrategia alcista tenía una posición de $100 abierta y el mercado cambiaba a BEAR, la estrategia se desactivaba, **dejaba de calcular indicadores y no evaluaba sus condiciones de Take Profit ni Stop Loss**. La posición se quedaba desamparada y expuesta a pérdidas masivas.

### Modificaciones y Archivos
Se eliminó la barrera dura de las **27 estrategias activas** y se reemplazó por un **Soft Guard** a nivel transaccional.

**Script Creado:** `scripts/refactor_regime_guards.py`
Se desarrolló y ejecutó un script en Python que analizó estructuralmente todos los archivos, borró los Hard Guards iniciales, e inyectó los Soft Guards de forma controlada justo antes de los métodos de compra.

**Archivos Corregidos (Ejemplos representativos):**
- **Crypto:** `strat_01_ema_cross.py`, `strat_03_grid_spot.py`, `strat_04_smart_twap.py`...
- **ETFs:** `strat_02_donchian.py`, `strat_06_bollinger.py`, `strat_10_grid.py`...
- **Equities:** `strat_02_vcp.py`, `strat_05_gamma_squeeze.py`, `strat_10_sector_rotation.py`.

**Refactorización Manual (Casos de borde):**
- **Estrategias con `buy_bracket` o lógicas complejas** (VCP, Gamma Squeeze, Sector Rotation, Grid ETF) no encajaban perfectamente en el parser del script, por lo que se auditaron y corrigieron manualmente inyectando el código en el punto exacto de la entrada algorítmica sin romper los if/elif subsecuentes.

**Lógica Implementada:**
```python
# EJEMPLO DEL NUEVO FLUJO
def on_bar(self, bar):
    # 1. ACTUALIZA INDICADORES SIEMPRE
    self._closes.append(bar.close)
    
    # 2. EVALÚA STOP LOSS / TAKE PROFIT SIEMPRE (Proteger capital existente)
    if self._has_position and hit_stop_loss:
        await self.order_manager.sell()
        
    # 3. EVALÚA ENTRADAS: APLICACIÓN DEL SOFT GUARD
    if condition_to_buy and not self._has_position:
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER):
            return # Se aborta solo la compra
        await self.order_manager.buy()
```

**Resultado:** Sincronización perfecta. Ahora el `RegimeManager` controla si una estrategia *puede abrir posiciones nuevas*, pero permite que las estrategias *gestionen y cierren las posiciones pre-existentes* basándose en la volatilidad minuto a minuto, garantizando que el Stop Loss se respete bajo cualquier clima del mercado.
