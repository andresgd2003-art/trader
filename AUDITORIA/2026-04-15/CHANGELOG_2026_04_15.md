# Changelog y Auditoría de Fixes
**Fecha:** 2026-04-15

Este archivo documenta las correcciones críticas realizadas como resultado de la auditoría y diagnóstico del sistema en el VPS. Estas modificaciones previenen errores críticos en producción relacionados con concurrencia, balance y bases de datos.

## Fix P1 - Prevención de "Insufficient Balance" en Crypto (OrderManager Crypto)
**Descripción:** Las estrategias de cripto con frecuencia intentaban vender la posición completa calculando la cantidad a mano, lo que dejaba un remanente por errores de flotantes o bien intentaba vender más de lo disponible (insufficient balance).
**Archivos Afectados:**
- `engine/order_manager_crypto.py`
**Corrección:**
Se añadió validación defensiva justo antes de mandar órdenes de `SELL` a Alpaca. Se obtiene la posición real llamando a `get_open_position()` y su atributo `qty_available`. Si la cantidad pedida es mayor a `qty_available`, se clampa a esta cantidad. Si la posición no existe, se aborta silenciosamente para evitar que el API arroje error HTTP.

## Fix P3 - Reducción de Latencia y HTTP 429 en API Server (STATE_CACHE)
**Descripción:** La capa de caching en `api_server.py` no cubría todo el flujo. Endpoints de estadisticas de estrategias (`/api/strategy/stats`), gráficos históricos (`_build_charts_task`) y barras por símbolo (`/api/symbol/history/...`) golpeaban el API de Alpaca directamente con GetOrdersRequest limit=1000 sin tregua.
**Archivos Afectados:**
- `api_server.py`
**Corrección:**
Se expandió la estructura `STATE_CACHE` añadiendo `orders_full` y `symbol_bars`. La lógica recabada en el worker de background reconstruye pasivamente y provee el histórico a los endpoints, eliminando cuellos de botella y mitigando los rate-limits (HTTP 429). 

## Fix P4 - Mitigación del Split-Brain de SQLite (Base de datos hardcodeada)
**Descripción:** Varios módulos conservaban la directiva `/app/data` proveniente de la época de Docker, provocando escrituras concurrentes que dividían el estado en dos silos en entornos donde el sistema se ejecuta bajo `systemd` dentro de `/opt/trader/data`. En consecuencia, los historiales (como el filtro sentmient o los log modes) sufrían borrados periódicos.
**Archivos Afectados:**
- `strategies_crypto/strat_08_ema_ribbon.py`
- `strategies_crypto/strat_10_sentiment.py`
- `strategies_crypto/strat_04_smart_twap.py`
- `engine/logger.py`
- `engine/stock_scorer.py`
- `engine/daily_mode.py`
- `main.py`
- `.env.example`
**Corrección:**
Se actualizó el fallback de las directivas os.environ en cada componente. El path por defecto será siempre `/opt/trader/data/` en lugar de `/app/data/`, garantizando así su consistencia con las rutinas de logrotate y el Unit systemd actual. Todos los cambios dentro del código están señalizados con `[P4 FIX - 2026-04-15]`.
