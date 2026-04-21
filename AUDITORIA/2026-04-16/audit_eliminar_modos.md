# Auditoría: Eliminación del Sistema de Modos (Daily Mode A/B/C)

## 📌 Resumen Ejecutivo
El sistema de "Daily Modes" (donde el bot rota entre Régimen, Filtro de Noticias y Scoring Dinámico cada día) está **profundamente entrelazado** en la columna vertebral del proyecto. Actúa no solo como un interruptor lógico, sino como el sello de agua (metadata) incrustado en cada transacción financiera que el bot envía a Alpaca.

**Dificultad Estimada:** Media - Alta 🟠
**Riesgo:** Alto (Si no se hace con cuidado, el Dashboard quedará ciego a las operaciones y FastAPI crasheará).
**Tiempo estimado de refactorización:** ~1 Hora.

---

## 🔍 Análisis de Impacto (Archivos Afectados)

### 1. El Núcleo de Órdenes y Parser (Alta Peligrosidad)
Tu bot etiqueta cada orden a Alpaca con el modo activo (ej. `strat_MACD_mA_12345678`). Si borramos los modos, la estructura de la base de datos se rompe.
*   **`engine/daily_mode.py`**: Se eliminaría por completo.
*   **`engine/order_manager.py` | `order_manager_crypto.py` | `order_manager_equities.py`**: Habrá que reescribir el generador del `client_order_id` para que ya no inyecte etiquetas `mA`, `mB` o `mC`.
*   **`engine/order_meta.py`**: Este archivo funciona con un `RegEx` estructurado para leer el modo desde Alpaca. Si ya no vienen modos, la lógica de parseo fallará estrepitosamente o lanzará Excepciones.

### 2. Estrategias (Impacto Medio)
Algunas estrategias en bolsa de valores fueron construidas para actuar distinto si están en "Día de Cuánticas" (Score) o en "Día Normal".
*   **`strategies_equities/strat_02_vcp.py`**
*   **`strategies_equities/strat_10_sector_rotation.py`**
*   Ambas estrategias importan `get_active_mode()` en su código para decidir si consumen las puntuaciones matemáticas del `engine/stock_scorer.py`. Al eliminar modos, tenemos que decidir si este Scorer estará siempre encendido o siempre apagado.

### 3. El Filtro de Noticias y Cortafuegos de Acciones
*   El **Order Manager de Equities** tiene un Firewall que detiene compras previendo desastres si hay noticias malas: `if get_active_mode() == "B": ...` (Línea 204). 
*   Si se quita el modo, la decisión debe ser directa: ¿Blindamos el bot verificando noticias el 100% del tiempo para siempre, o matamos la "Skill" de NewsRiskFilter permanentemente?

### 4. El Dashboard Visual (Frontend & Backend)
*   **`api_server.py`**: Los endpoints `/api/reports` y `/api/clock` exponen cuál modo rige hoy para pintarlo en tu pantalla (UI). Habrá que amputar eso.
*   **`static/index.html` y los .JS**: El banner superior que dice "Daily Mode: Filtro Noticias..." o el desglose de "Profit por Modo" deberán ser reestructurados.

### 5. Suite de Pruebas
*   **`tests/test_sanity.py`**: Toda prueba unitaria de aserción sobre `_determine_mode()` va a estallar y deberá borrarse.

---

## 🛠️ Conclusión y Recomendación

Eliminar el sistema es **totalmente viable**, pero **no es cambiar solo 3 líneas**. Toca unas 14 piezas de arquitectura diferentes.

**¿Deberías hacerlo?**
*   **Si lo ideal es la simplicidad:** Sí. Tener un bot monolítico te quita problemas de "hoy el bot no invirtió porque amaneció en Modo B (Noticias)". Podríamos fusionar lo mejor de los 3 mundos: dejar el *StockScorer* operando como radar base siempre, y dejar el *News Filter* activo siempre como guardia, sin rotar diariamente.
*   **Si lo haces por miedo:** No lo aconsejo todavía. Es una gran cualidad del proyecto. Deberías ver cómo sobrevive este primer mes ciclando rutinas, tal como acordamos en la sugerencia cuantitativa anterior.

---
*Reporte generado por Inteligencia Artificial - Guardado en Auditoría Histórica.*
