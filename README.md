# AlpacaNode Trading Engine v2.0

Sistema de trading algorítmico avanzado que ejecuta de manera asíncrona **30 estrategias en paralelo** sobre 3 Ecosistemas independientes (ETF, Cripto 24/7 y Equities Singulares) utilizando la Paper Trading API de Alpaca. 

**Estado del Despliegue:** Operativo en VPS (Dockerizado).

## 🚀 Innovaciones Tecnológicas y Arquitectura

- **Central Dispatcher (WebSocket):** Una única y eficiente conexión hacia Alpaca IEX Market Data. Los datos de velas (bars) y cotizaciones se distribuyen internamente a las estrategias de cada ecosistema.
- **Micro-Motores:**
  - `main_equities.py`: Escáner pre-mercado automático (09:00 AM EST) para detectar "ganadores" y "perdedores" dinámicamente. Implementa *Regime Manager* (BULL/BEAR/CHOP) y detiene trading vía "Circuit Breakers".
  - `main_crypto.py`: Motor 24/7 de baja latencia con reconexión nativa enfocado a pares de BTC, ETH, SOL, LINK y BCH.
  - `main.py` (ETFs Core): Conecta los 3 sub-motores.
- **FastAPI Dashboard:** Interfaz gráfica accesible vía web operando en multi-hilos para exponer las ganancias, P&L dinámico y reportes CSV históricos actualizados cada 15 segundos sin interrupción en el hilo principal.
- **Modelos Cuantitativos Avanzados:** Procesamiento de lenguaje natural (NLP FinBERT) para Sentiment de Noticias Finacieras, y *Stat-Arb* de Pares.

## 📁 Estructura del Nodo Raíz

```text
trader/
├── main.py                      # Distribuidor principal (Engine Core + WebSockets)
├── main_equities.py             # Rutinas pre-mercado y estrategias de Acciones Volátiles
├── main_crypto.py               # Ecosistema independiente Cripto
├── api_server.py                # Backend del Dashboard (FastAPI) y Descarga CSV
├── vps_build.py                 # Script local para recarga de contenedores en VPS remoto
├── Dockerfile                   # Configuración del servidor Docker
├── .env.example                 # Plantilla de variables (Ignorado en GIT)
├── engine/                      # Módulos Core (logger, notifier, managers, screener, NLP)
├── static/
│   └── index.html               # Frontend Interactivo del Web Dashboard
├── strategies_equities/         # Estrategias (Gamma Squeeze, VCP, Insiders...)
├── strategies_crypto/           # Estrategias (Arb, Funding Rates, VWAP Touch...)
└── strategies/                  # Estrategias Clásicas ETF (Momentum, Grid...)
```

## 📊 Sistema de Estrategias y Régimen

El bot evalúa diariamente si el mercado se encuentra en modo alcista (**BULL**), bajista (**BEAR**) o en rango lateral (**CHOP**), utilizando un análisis de las SPY-SMA y VIX. Únicamente las estrategias congruentes con el régimen del mercado tienen permitido operar cada sesión.

* Cada ecosistema opera sus 10 estrategias especializadas.
* El P&L diario se registra basándose en el capital de la cuenta del cierre de mercado anterior.
* Exportación total: `/api/reports?period=all` brinda bitácoras con el *P&L Realizado Individual* mapeado orden por orden e impreso en CSV.

## 🐳 Despliegue en VPS (Producción)

Los repositorios locales han sido unificados (`andresgd2003-art/trader`). Para actualizar el VPS tras realizar ediciones de código:
```bash
# Sube y haz commit de las modificaciones primero:
git commit -am "Cambios"
git push

# Obliga al servidor remoto a recargar en caliente
python vps_build.py
```
