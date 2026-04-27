# Backtest: 3 Estrategias Bajistas/Defensivas

**Periodos:** COVID-crash 2020 | Bear market 2022
**Portfolio inicial:** $200 | **Slippage:** 0.1% | **Data:** Daily (yfinance)
**Warmup:** 300 dias previos para inicializar SMA200/MACD/RSI

> Nota: Backtest en escala DAILY. En produccion las estrategias usan barras de 5m.
> Los resultados en daily son mas conservadores (menos senales) pero validan la logica.

## 2020

| Estrategia | Trades | Win% | PnL$ | PnL% | MaxDD% | Sharpe |
|---|---|---|---|---|---|---|
| InverseMomentumETF | 17 | 41% | $-5.8818 | -2.94% | -6.41% | -2.221 |
| DefensiveRotation | 4 | 75% | +$0.3292 | +0.16% | 0.0% | 2.291 |
| CryptoMeanReversionExtreme | 2 | 100% | +$11.7866 | +5.89% | 0.0% | 57.519 |

## 2022

| Estrategia | Trades | Win% | PnL$ | PnL% | MaxDD% | Sharpe |
|---|---|---|---|---|---|---|
| InverseMomentumETF | 60 | 43% | +$2.5052 | +1.25% | -3.21% | 0.542 |
| DefensiveRotation | 13 | 62% | +$0.4239 | +0.21% | -1.16% | 0.741 |
| CryptoMeanReversionExtreme | 6 | 33% | $-1.3573 | -0.68% | -2.46% | -1.63 |

## Veredicto

- **InverseMomentumETF** | 2020: PERDIDA -$5.8818 | 2022: RENTABLE +$2.5052
- **DefensiveRotation** | 2020: RENTABLE +$0.3292 | 2022: RENTABLE +$0.4239
- **CryptoMeanReversionExtreme** | 2020: RENTABLE +$11.7866 | 2022: PERDIDA -$1.3573

## Advertencias Importantes

1. **ETFs 3x inversos (SQQQ/SPXU):** tienen decay por rebalanceo diario que no se captura en un backtest diario — en produccion real el decay REDUCE las ganancias.
2. **DefensiveRotation:** KO/PG/JNJ/WMT/PEP son defensivos pero no inmunes — en crashes sistemicos bajan con el mercado aunque menos.
3. **CryptoMeanReversionExtreme:** los rebounds extremos (RSI<25) son raros en daily. En 5m (produccion) ocurren mucho mas frecuentemente.

*Generado: 2026-04-24 14:56*
