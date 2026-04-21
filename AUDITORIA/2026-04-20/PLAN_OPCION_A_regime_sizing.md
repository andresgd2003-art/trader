# PLAN — Opción A: Eliminar filtro de régimen, ajustar tamaño de posición
**Fecha:** 2026-04-20  
**Autor:** Claude (análisis + plan, SIN cambios aplicados)  
**Estado:** BORRADOR — pendiente de aprobación

---

## Resumen ejecutivo

En lugar de deshabilitar estrategias enteras por régimen de mercado (comportamiento actual),
todas las estrategias correrán siempre, pero el `OrderManager` calculará un `notional`
**más pequeño en BEAR** y **normal en BULL/CHOP**.

Beneficio principal: el bot no queda "mudo" en mercados bajistas. Captura los violentos
bear-market rallies con posiciones más pequeñas en vez de no hacer nada.

Fuente académica: fondos sistemáticos profesionales no deshabilitan estrategias por régimen —
diversifican por descorrelación. Correlación de drawdown entre trend-following y mean-reversion
es -0.13 (fuente: algomatictrading.com).

---

## Diagnóstico del problema actual

| Régimen | ETF estrategias activas | ETF estrategias congeladas |
|---------|------------------------|---------------------------|
| BULL    | 4 de 9 (strat 8 comentada) | 5 congeladas |
| BEAR    | 3 de 9                 | **6 congeladas** |
| CHOP    | 4 de 9                 | 5 congeladas |

En BEAR (régimen actual por tariff shock), solo Bollinger (6), VIX Filter (7) y Grid (10)
están activas. Golden Cross, Donchian, MACD, RSI Dip, Pairs Trading: todas congeladas.

Problema adicional: VWAP (strat 8) está comentada permanentemente en `strategies/__init__.py`
sin razón técnica clara y por eso también figura en el mapa BULL como slot vacío.

---

## Archivos que se modificarían (7 archivos total)

```
engine/order_manager.py          ← CAMBIO PRINCIPAL: sizing dinámico por régimen
engine/regime_manager.py         ← Limpieza de mapas obsoletos (opcional)
strategies/__init__.py           ← Descomentar VWAP (strat 8)
strategies/strat_08_vwap.py      ← Fix bug: buy()/sell() usan qty= incompatible
main.py                          ← Registrar VWAPBounceStrategy
strategies/strat_XX.py (×9)     ← Eliminar bloqueo is_strategy_enabled() de on_bar
strategies_equities/strat_XX.py (×6) ← Ídem para equities
strategies_crypto/strat_XX.py (×10) ← Ídem para crypto (opcional, ver nota)
```

---

## CAMBIO 1 — `engine/order_manager.py` (el más importante)

### Qué hace ahora
Línea 127 calcula siempre el 4% del settled_cash sin importar el régimen:
```python
dynamic_notional = round(settled_cash * 0.04, 2)
```

### Qué haría después
Antes de calcular el notional, consultar el régimen actual y aplicar un porcentaje distinto:

```python
# En _execute_order(), ANTES de la línea 127 actual
from engine.regime_manager import get_current_regime

regime_data  = get_current_regime()
regime_str   = regime_data.get("regime", "UNKNOWN")

# Escala de riesgo por régimen
REGIME_NOTIONAL_PCT = {
    "BULL":    0.04,   # 4% — máximo agresividad, mercado favorable
    "CHOP":    0.03,   # 3% — moderado, mercado lateral
    "BEAR":    0.02,   # 2% — conservador, mercado bajista
    "UNKNOWN": 0.02,   # 2% — seguro por defecto si no hay datos
}
pct = REGIME_NOTIONAL_PCT.get(regime_str, 0.02)

dynamic_notional = round(settled_cash * pct, 2)
logger.info(f"[OrderManager] Sizing régimen {regime_str}: {pct*100:.0f}% → ${dynamic_notional}")
```

### Dónde exactamente en el archivo
- Archivo: `engine/order_manager.py`
- Línea actual a reemplazar: **línea 127**
  ```python
  dynamic_notional = round(settled_cash * 0.04, 2)
  ```
- Por el bloque de 12 líneas mostrado arriba.

### Por qué funciona sin riesgos
- `get_current_regime()` retorna el dict global `_CURRENT_REGIME` — no hace llamadas
  de red, es instantáneo (O(1)).
- Si el régimen aún es UNKNOWN al arrancar, usa 2% (conservador).
- No cambia ninguna lógica de SELL (vende posición completa siempre, independiente del régimen).

---

## CAMBIO 2 — `strategies/__init__.py` (descomentar VWAP)

### Estado actual (líneas 11-12)
```python
# from .strat_08_vwap import VWAPBounceStrategy
```
```python
# "VWAPBounceStrategy",
```

### Estado objetivo
```python
from .strat_08_vwap import VWAPBounceStrategy
```
```python
"VWAPBounceStrategy",
```

---

## CAMBIO 3 — `strategies/strat_08_vwap.py` (fix bug crítico)

### Bug encontrado
VWAP usa la firma `buy(symbol, qty=, strategy_name=)` pero el `OrderManager` ETF
NO acepta el parámetro `qty`. Si se activa con el bug, lanza `TypeError` silencioso
y nunca ejecuta órdenes.

Firma actual del OrderManager:
```python
async def buy(self, symbol: str, strategy_name: str = ""):   # sin qty
async def sell(self, symbol: str, strategy_name: str = ""):  # sin qty
```

### Líneas bugueadas en strat_08_vwap.py
- **Línea 101** (BUY):
  ```python
  # ACTUAL (roto):
  await self.order_manager.buy(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
  
  # CORREGIDO:
  await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
  ```
- **Línea 114** (SELL en EOD):
  ```python
  # ACTUAL (roto):
  await self.order_manager.sell(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
  
  # CORREGIDO:
  await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
  ```

### Consecuencia secundaria
El atributo `QTY = 20` queda obsoleto con notional sizing. Se puede eliminar la línea
o dejarla como dead code inofensivo.

---

## CAMBIO 4 — `main.py` (registrar VWAP)

### Estado actual en `_register_strategies()`
```python
from strategies import (
    GoldenCrossStrategy,
    DonchianBreakoutStrategy,
    MomentumRotationStrategy,
    MACDTrendStrategy,
    RSIDipStrategy,
    BollingerReversionStrategy,
    VIXFilteredReversionStrategy,
    PairsTradingStrategy,
    GridTradingStrategy,
)
```

### Estado objetivo (agregar VWAPBounceStrategy)
```python
from strategies import (
    GoldenCrossStrategy,
    DonchianBreakoutStrategy,
    MomentumRotationStrategy,
    MACDTrendStrategy,
    RSIDipStrategy,
    BollingerReversionStrategy,
    VIXFilteredReversionStrategy,
    VWAPBounceStrategy,          # ← añadir esta línea
    PairsTradingStrategy,
    GridTradingStrategy,
)
```

En `_register_strategies()`, añadir la instancia (línea ~143, justo antes de PairsTrading):
```python
VWAPBounceStrategy(order_manager=self.order_manager, regime_manager=rm),
```

Y actualizar el log de línea ~145:
```python
# ACTUAL:
logger.info(f"[Engine] {len(strategies)} estrategias ETF registradas.")
# SIGUE IGUAL — len(strategies) se auto-actualiza, no hace falta tocar nada más
```

---

## CAMBIO 5 — Eliminar `is_strategy_enabled()` de las estrategias ETF

Cada estrategia tiene un bloque como este en `on_bar()`:
```python
if self.regime_manager and not self.regime_manager.is_strategy_enabled(N):
    return
```
Este bloqueo hace que la estrategia no procese ninguna barra cuando el régimen no la incluye.
Con la Opción A, ese bloqueo desaparece — el sizing del OrderManager es el único control de riesgo.

### Archivos y líneas exactas a modificar

| Archivo | Línea aprox. | Número de estrategia |
|---------|-------------|---------------------|
| `strategies/strat_01_macross.py` | ~línea donde aparece `is_strategy_enabled(1)` | 1 |
| `strategies/strat_02_donchian.py` | `is_strategy_enabled(2)` | 2 |
| `strategies/strat_03_rotation.py` | dentro de `_rotate()`, `is_strategy_enabled(3)` | 3 |
| `strategies/strat_04_macd.py` | `is_strategy_enabled(4)` | 4 |
| `strategies/strat_05_rsi_dip.py` | `is_strategy_enabled(5)` | 5 |
| `strategies/strat_06_bollinger.py` | `is_strategy_enabled(6)` | 6 |
| `strategies/strat_07_vix_filter.py` | `is_strategy_enabled(7)` | 7 |
| `strategies/strat_08_vwap.py` | `is_strategy_enabled(8)` | 8 |
| `strategies/strat_09_pairs.py` | `is_strategy_enabled(9)` | 9 |
| `strategies/strat_10_grid.py` | `is_strategy_enabled(10)` | 10 |

### Patrón de cambio (igual para todos)
```python
# ELIMINAR estas 2 líneas:
if self.regime_manager and not self.regime_manager.is_strategy_enabled(N):
    return
```
No reemplazar por nada — simplemente borrar las 2 líneas.

---

## CAMBIO 6 — Estrategias Equities (mismo patrón)

Las 6 estrategias equities también tienen el bloqueo. Misma operación:

| Archivo | Estrategia |
|---------|-----------|
| `strategies_equities/strat_02_vcp.py` | VCP |
| `strategies_equities/strat_04_pead.py` | PEAD |
| `strategies_equities/strat_05_gamma_squeeze.py` | Gamma Squeeze |
| `strategies_equities/strat_08_nlp_sentiment.py` | NLP Sentiment |
| `strategies_equities/strat_09_insider_flow.py` | Insider Flow |
| `strategies_equities/strat_10_sector_rotation.py` | Sector Rotation |

**Nota importante:** El `OrderManagerEquities` tiene un cap fijo de `MAX_POSITION_USD = 100.0`.
Para que equities también respete el régimen de sizing, habría que hacer el mismo cambio
que en el Cambio 1 pero en `engine/order_manager_equities.py`. Esto es opcional —
ya tienen un cap muy conservador.

---

## CAMBIO 7 (OPCIONAL) — Crypto strategies

Las 10 estrategias crypto también tienen el bloqueo. La diferencia es que el
`OrderManagerCrypto` ya tiene su propio sistema de caps dinámicos (DAY_CAP=15, NIGHT_CAP=40).
El bloqueo por régimen en crypto es menos crítico pero se puede eliminar igualmente
para que todas corran siempre.

---

## `engine/regime_manager.py` — ¿Qué pasa con los mapas?

Los mapas `REGIME_ETF_MAP`, `REGIME_CRYPTO_MAP`, `REGIME_EQUITIES_MAP` quedan sin efecto
porque ninguna estrategia los consultará más. Se pueden:

**Opción A1 (recomendada):** Dejarlos como están — no hacen daño y documentan la intención original.  
**Opción A2:** Eliminarlos para limpiar el código.

El método `is_strategy_enabled()` del `RegimeManager` quedaría como código muerto.
El régimen sigue evaluándose cada hora (para informar el sizing del OrderManager).

---

## Resumen de impacto por motor

### Motor ETF (antes → después)
| Régimen | Antes | Después |
|---------|-------|---------|
| BULL | 4 activas al 4% | **10 activas** al 4% |
| BEAR | 3 activas al 4% | **10 activas** al 2% |
| CHOP | 4 activas al 4% | **10 activas** al 3% |

### Motor Equities (antes → después)
| Régimen | Antes | Después |
|---------|-------|---------|
| BULL | 5 activas al $100 | **6 activas** al $100 |
| BEAR | 2 activas al $100 | **6 activas** al $100 |
| CHOP | 2 activas al $100 | **6 activas** al $100 |

### Motor Crypto (antes → después)
| Régimen | Antes | Después |
|---------|-------|---------|
| BULL | 6 activas | **10 activas** |
| BEAR | 4 activas | **10 activas** |
| CHOP | 4 activas | **10 activas** |

---

## Riesgos y mitigaciones

| Riesgo | Probabilidad | Mitigación |
|--------|-------------|-----------|
| Demasiadas órdenes simultáneas | Media | La cola del OrderManager procesa de a una, con 0.4s de delay entre órdenes. No hay riesgo de rate-limit. |
| Estrategias trend-following pierden en BEAR | Media | El sizing al 2% limita la pérdida máxima por trade a la mitad. El portfolio_manager/circuit_breaker de equities sigue activo. |
| VWAP usa qty hardcodeado (QTY=20) | Alta (ya existe) | Resuelto en Cambio 3 — simplemente quitamos qty del call. |
| Pairs Trading abre shorts en BEAR | Baja | Pairs tiene su propio guard `_has_real_position()`. Solo opera si hay divergencia de Z-score > 2.0, que es infrecuente. |
| Momentum Rotation rota a sector incorrecto | Baja | Rotation solo ejecuta en viernes 15:30. El sizing 2%-4% limita exposición. |

---

## Orden de ejecución (si se aprueba)

1. `engine/order_manager.py` — Cambio 1 (sizing por régimen)
2. `strategies/strat_08_vwap.py` — Cambio 3 (fix bug qty)
3. `strategies/__init__.py` — Cambio 2 (descomentar VWAP)
4. `main.py` — Cambio 4 (registrar VWAP)
5. Eliminar `is_strategy_enabled()` de las 10+6+10 estrategias — Cambios 5/6/7
6. Commit único con mensaje descriptivo
7. Push → VPS hace git pull automático en el próximo restart (04:00 ET)

---

## Verificación post-deploy

```bash
# En VPS, después del restart a las 4am ET:
journalctl -u alpacatrader -n 50 | grep "estrategias ETF registradas"
# Esperado: "10 estrategias ETF registradas"

journalctl -u alpacatrader -n 100 | grep "Sizing régimen"
# Esperado: "Sizing régimen BEAR: 2% → $XX.XX"

journalctl -u alpacatrader -n 100 | grep "VWAP Bounce"
# Esperado: líneas de log con precio y VWAP calculado
```
