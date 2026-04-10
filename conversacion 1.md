# Historial Completo y Reglas del Proyecto (AlpacaNode Trading Engine)

Este documento contiene el backup de las instrucciones originales para el motor de estrategias.

## 1. Contexto de Proyecto Original (Equities Engine)
A continuación se recupera el prompt original que define el comportamiento del sistema para acciones:

```text
SYSTEM SPECIFICATION: ALPACA EQUITIES ALGO TRADING ENGINE
PROTOCOL: MACHINE-TO-MACHINE (M2M) INSTRUCTION SET
TARGET AGENT: Antigravity Dev AI
PROJECT CONTEXT: Deploy an algorithmic trading framework for INDIVIDUAL US STOCKS on an Ubuntu VPS via Easypanel. System will run 10 distinct strategies, managed by a "Market Regime Alternator". FOCUS: High Volatility, Asymmetric Risk, Dynamic Universe (Small Caps/Gappers).

DIRECTIVE 1: API, DATA & DYNAMIC UNIVERSE (CRITICAL)
Dynamic Daily Screener (The Alternator Engine): The bot MUST NOT use a static universe (like S&P 500). Instruct Antigravity to build a PreMarketScreener class.
Action: Every day at 09:00 AM EST, use the Alpaca Top Movers API or Polygon.io snapshot API to fetch the top 20 stocks gapping up/down with the highest pre-market volume.
Filter: Price between $1 and $25, Volume > 500k. Limit the daily tradeable universe to ONLY these dynamic tickers.
```

## 2. Resolviendo Discrepancias y Errores (Gemini 3.1 Pro)

El usuario detectó que el dashboard presentaba **discrepancias con las cantidades de P&L**, **ausencia de gráficos históricos**, y reportó que el agente anterior (Flash) no analizó adecuadamente el contexto ni solucionó los errores en el VPS, persistiendo el límite de conexión y un dashboard roto.

**¿Qué ocurrió realmente?**
El agente anterior realizó los cambios de código, pero por un problema en la configuración local de `git`, subió el código de trading a otro repositorio tuyo llamado `VIDEO-APPINVENTARIO` en lugar de `trader`. Como consecuencia, cuando el VPS (Hostinger) se actualizaba, descargaba el mismo código antiguo y nunca recibió nuestros arreglos del WebSockets ni el nuevo Dashboard histórico.

**Solución Implementada**:
1.  **Limpieza del Repositorio**: Hemos reconfigurado tu carpeta local para que apunte a `https://github.com/andresgd2003-art/trader.git` y subido nuestros cambios de conexión de Alpaca.
2.  **Dashboard Histórico (Corregido)**: Añadimos P&L dinámico basado en tu equity de Alpaca y botones temporales de 1D, 1W, 1M, 1A (alojados ahora correctamente en la UI actual).
3.  **Límite Conexión 406**: Se ratificó delay de 20 segundos para estabilizar la sesión en cada despliegue del VPS.
