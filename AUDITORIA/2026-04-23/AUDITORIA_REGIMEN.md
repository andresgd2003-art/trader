# Auditoría de Régimen de Mercado (Regime Manager)

**Fecha de Auditoría:** 23 de Abril de 2026
**Componente Analizado:** `engine/regime_manager.py`

---

## 1. ¿Están funcionando verdaderamente los cambios de régimen?

**Respuesta Corta:** **NO.** 
El cálculo matemático del régimen funciona y se evalúa correctamente, pero **sus efectos han sido completamente anulados ("bypassed")** en el código. Actualmente, el régimen no tiene ningún impacto real ni en la selección de estrategias ni en el tamaño de las posiciones.

### Hallazgos Críticos:
1. **Mapas de Estrategias Anulados:** Las estrategias que se habilitan por cada régimen están definidas en diccionarios (`REGIME_ETF_MAP`, `REGIME_CRYPTO_MAP`, `REGIME_EQUITIES_MAP`). Sin embargo, en todos los regímenes (`BULL`, `BEAR`, `CHOP`, `UNKNOWN`), se están asignando **exactamente las mismas estrategias**. 
   - *Ejemplo:* En Equities, el mapa activa las estrategias `[2, 4, 5, 8, 9, 10]` sin importar si el mercado está alcista, bajista o lateral. Esto significa que el filtro de régimen no está apagando ni encendiendo estrategias.
2. **Sizing Inoperante:** El código calcula un *sizing* sugerido (`4%` para BULL, `3%` para CHOP, `2%` para BEAR), pero **solo lo usa para imprimirlo en los logs** (`logger.info`). Esta variable no se exporta al estado global `_CURRENT_REGIME` y, por ende, el `portfolio_manager` o el `order_manager` no están recibiendo ni aplicando este tamaño dinámico de posición.

---

## 2. Niveles y Lógica Actual de Detección

El motor determina el régimen evaluando la relación entre el precio del **SPY** y su **Media Móvil de 200 días (SMA200)**, en combinación con el nivel del índice de volatilidad **VIX**.

| Régimen | Condición SPY | Condición VIX | Comportamiento Teórico (No aplicado actualmente) |
| :--- | :--- | :--- | :--- |
| **BULL** | Precio SPY > SMA 200 | VIX < 30.0 | Habilitar momentum / Sizing: 4% |
| **BEAR** | Precio SPY < SMA 200 | VIX > 25.0 | Habilitar reversión a la media / Sizing: 2% |
| **CHOP** | Cualquier otro caso | Cualquier otro caso | Habilitar rotación de sectores / Sizing: 3% |

**Inconsistencias detectadas:**
- La documentación interna menciona que el umbral BULL para el VIX es `< 20`, pero en el código la constante es `self.VIX_BULL_THRESHOLD = 30.0`. Esto hace que sea extremadamente fácil que el sistema se declare en BULL.
- Hay una "zona gris" o superposición si el VIX está entre 25 y 30.

---

## 3. Fuentes de Información para el Régimen

El bot obtiene sus métricas de dos fuentes principales dentro del método `assess()`:

1. **SPY (S&P 500 ETF):** Utiliza la API de datos históricos de Alpaca (`StockHistoricalDataClient`) para obtener los últimos 300 días y calcular el precio de cierre actual vs la SMA de 200 períodos.
2. **VIX (Volatilidad):** 
   - Inicialmente, el código solicita datos para el ETF `VIXY` usando Alpaca, pero **nunca utiliza estos datos**.
   - En su lugar, hace una importación local de la librería `yfinance` e intenta obtener el ticker `^VIX` directamente de Yahoo Finance.
   - **Fallo de seguridad (Fallback):** Si `yfinance` falla o se queda sin conexión, el sistema asigna un valor por defecto de `20.0`. Dado que `20.0 < 30.0`, si el SPY está sobre la SMA200 y Yahoo Finance falla, el bot asumirá agresivamente un régimen BULL sin tener la volatilidad real. Jamás podrá entrar en BEAR si ocurre este fallo.

---

## 4. Propuestas de Mejora y Modernización (Basadas en IA Context7 & Brave Search)

A partir de la investigación en arquitecturas modernas de trading cuantitativo en Python, se proponen las siguientes mejoras para transformar el régimen de un simple filtro condicional a un modelo predictivo e integrado:

### A. Mejoras Inmediatas (Código Core)
- **Activar el "Regime-Specific Sizing":** Modificar `_CURRENT_REGIME.update()` para incluir la clave `"suggested_sizing": 0.04` (etc) y consumir este valor dentro de `portfolio_manager.py` al calcular la cantidad de acciones a comprar (`qty`).
- **Restaurar el Filtrado de Estrategias:** Reconfigurar los mapas para que realmente aíslen estrategias de momentum a mercados BULL y estrategias de reversión a la media a mercados BEAR.
- **Corregir Fuente del VIX:** Eliminar la dependencia de `yfinance` e implementar el cálculo utilizando los datos de `VIXY` (que ya se están descargando de Alpaca exitosamente).
- **Ajustar Umbrales:** Sincronizar el código con la lógica de negocio bajando el `VIX_BULL_THRESHOLD` a 20.0 para ser más conservadores.

