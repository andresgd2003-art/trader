# Auditoría de Estrategias: Compatibilidad con Cash Account ~$500 USD
**Fecha:** 20 de Abril de 2026
**Contexto:** El sistema fue diseñado originalmente para cuentas con mayor capital. Al operar con ~$200-500 USD en Cash Account, varias estrategias se ven limitadas o completamente inoperantes.

---

## MOTOR ETF (10 Estrategias)

### ✅ Funcionando correctamente (7/10)

| # | Estrategia | Estado | Notas |
|---|-----------|--------|-------|
| 1 | Golden Cross (SMA50/200) | ✅ OK | Usa sizing dinámico del OrderManager (4%=$20) |
| 2 | Donchian Breakout | ✅ OK | Compra/vende IWM, sizing dinámico |
| 4 | MACD Momentum | ✅ OK | Opera TQQQ, sizing dinámico |
| 5 | RSI Buy the Dip | ✅ OK | Opera TQQQ en sobreventa |
| 6 | Bollinger Band | ✅ OK | Opera SMH, sizing dinámico |
| 7 | RSI + VIX Filter | ✅ OK | Opera SPY con filtro VIX |
| 8 | VWAP Bounce | ✅ OK | Opera SMH, sizing dinámico |

### ⚠️ Funcionando con limitaciones (2/10)

| # | Estrategia | Problema | Impacto |
|---|-----------|----------|---------|
| 3 | **Momentum Rotation** | Opera XLY/XLF/XLV/XLE → estos NO están en `ALL_SYMBOLS` del WebSocket. Solo recibe barras históricas semanales. | Funciona por diseño (rotación semanal), pero las compras son $20 por posición con sizing al 4%, lo cual genera comisiones desproporcionadas vs ganancia. |
| 9 | **Pairs Trading** | **YA CORREGIDA** hoy → QQQ↔PSQ con ETF inverso. | ✅ Solucionada |

### 🔴 Parcialmente rota (1/10)

| # | Estrategia | Problema | Causa Raíz |
|---|-----------|----------|------------|
| 10 | **Grid Trading (SOXX)** | La grid coloca 5 Limit Orders de compra a -3%, -6%, -9%, -12%, -15% del baseline. Con $500 al 4% = **$20 por nivel**. Pero **no tiene lógica de VENTA automática**. Si una orden se llena, el capital queda atrapado. Además, el log imprime cada barra (CPU). | Solo compra, nunca vende. Estrategia incompleta. |

---

## MOTOR CRYPTO (10 Estrategias)

### ✅ Funcionando correctamente (6/10)

| # | Estrategia | Moneda | Estado | Notas |
|---|-----------|--------|--------|-------|
| 1 | EMA Cross | BTC/USD | ✅ OK | Cap $15 (día) / $40 (noche) aplicado correctamente |
| 2 | BB Breakout | ETH/USD | ✅ OK | Cap dinámico funcional |
| 5 | Funding Squeeze | ETH/USD | ✅ OK | Lee Binance funding rates |
| 6 | Vol Anomaly | LINK/USD | ✅ OK | Cap aplicado |
| 7 | Pair Divergence | BTC↔ETH | ✅ OK | Opera solo ETH, sin shorts |
| 9 | VWAP Touch | BTC/USD | ✅ OK | notional_usd=100 → capeado a $15/$40 |

### ⚠️ Limitadas por capital (2/10)

| # | Estrategia | Moneda | Problema |
|---|-----------|--------|----------|
| 4 | **Smart TWAP** | BTC/USD | `BASE_ALLOCATION = $50`, pero el cap del OrderManagerCrypto la reduce a $15 (día) o $40 (noche). Funciona, pero la acumulación horaria pierde sentido con montos tan pequeños. La TWAP necesita volumen para suavizar precio. |
| 10 | **Sentiment (Fear&Greed)** | BTC/USD | `notional_usd = $250` → capeado a $15. Hardcodea `self.current_qty = round(250.0 / bar.close, 5)` para rastrear su posición, pero el OrderManager envía solo $15. **Bug:** El `current_qty` interno no coincide con la qty real enviada, causando que las ventas intenten vender más de lo que posee. |

### 🔴 Rota / Inoperante (2/10)

| # | Estrategia | Moneda | Problema | Severidad |
|---|-----------|--------|----------|-----------|
| 3 | **Grid Spot** | **SOL/USD** | `TOTAL_ALLOCATION_USD = $1,000` dividido en 5 tranches de $200 cada una → el OrderManagerCrypto las capea a $15. Pero el problema real: **la grid solo tiene COMPRAS (bids)**. No existe la lógica de venta/take-profit. El código lo admite: *"Esto es una base simplificada"*. Las Limit Orders se colocan a -1.5%, -3%, etc. debajo del precio pero si SOL no cae no se ejecutan. Y si se ejecutan, no vende jamás. | **CRÍTICO** — Capital atrapado sin salida |
| 8 | **EMA Ribbon** | BCH/USD | `notional_usd = $100` → capeado a $15. Pero tiene el **mismo bug que Sentiment**: hardcodea `self.current_qty = round(100.0 / bar.close, 5)` y luego intenta vender esa cantidad. Si el OrderManager solo compró $15 de BCH, el `sell_exact()` pedirá vender ~6x más BCH del que realmente tiene. Con la validación defensiva del SELL eso se corrige automáticamente, pero **el tracking interno queda desincronizado**, lo que puede causar que nunca cierre la posición o no detecte que ya vendió. | **ALTO** — Desincronización qty |

---

## MOTOR EQUITIES (6 Estrategias activas)

### ✅ Corregidas hoy (2/6)

| # | Estrategia | Estado |
|---|-----------|--------|
| 2 | VCP Minervini | ✅ Corregida (macro/micro desacoplado) |
| 4 | PEAD Earnings | ✅ Corregida (calendar days + SMA50 real) |

### ⚠️ Limitadas (4/6)

| # | Estrategia | Problema |
|---|-----------|----------|
| 5 | Gamma Squeeze | Busca OPTIONS data (gamma exposure) — requiere API de opciones. Con $500 probablemente funcione solo como detector. |
| 8 | NLP Sentiment | Depende de news API para detectar patrones de sentimiento. Funcional pero rara vez dispara compras. |
| 9 | Insider Flow | Lee formularios SEC — funcional pero casi nunca detecta insiders en las acciones del universo del screener. |
| 10 | Sector Rotation | Opera múltiples sector ETFs — similar al Momentum Rotation del motor ETF, compras de ~$20 por posición. |

---

## RESUMEN EJECUTIVO

| Categoría | Total | ✅ OK | ⚠️ Limitada | 🔴 Rota |
|-----------|-------|-------|-------------|---------|
| ETF | 10 | 8 | 1 | 1 |
| Crypto | 10 | 6 | 2 | **2** |
| Equities | 6 | 2 | 4 | 0 |
| **TOTAL** | **26** | **16** | **7** | **3** |

## Las 3 Estrategias Rotas que requieren acción inmediata:

1. **🔴 Crypto Grid SOL/USD (strat_03)**: Sin lógica de venta. Capital atrapado. → Necesita reimplementación completa del ciclo BUY→SELL.
2. **🔴 Crypto EMA Ribbon BCH/USD (strat_08)**: Desincronización de qty interno vs real. → Necesita leer qty real de Alpaca después de cada compra.
3. **🔴 ETF Grid SOXX (strat_10)**: Sin lógica de venta automática. → Mismo problema que Grid Crypto.

## Las 2 Estrategias con Bug de Tracking:

4. **⚠️ Crypto Sentiment BTC (strat_10)**: `current_qty` hardcodeado a $250/price pero el cap envía $15.
5. **⚠️ Crypto Smart TWAP BTC (strat_04)**: Funcional pero la acumulación horaria a $15 por tramo pierde eficacia.
