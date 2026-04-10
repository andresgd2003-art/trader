# =============================================
# ALPACA TRADING ENGINE - DOCKERFILE
# =============================================
# Base: python:3.11-slim (requerido para asyncio + TA-Lib)
FROM python:3.11-slim

# Evitar prompts interactivos durante el build
ENV DEBIAN_FRONTEND=noninteractive

# Instalar dependencias mínimas del sistema
# Instalar dependencias mínimas del sistema y compilador C++ para TA-Lib
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Compilar TA-Lib nativo en C
RUN wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias Python primero (aprovecha cache de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY . .

# Crear directorio de datos (SQLite DB + logs)
# Este directorio se monta como volumen para persistencia
RUN mkdir -p /app/data

# Variables de entorno (se sobreescriben desde Easypanel)
ENV ALPACA_API_KEY=""
ENV ALPACA_SECRET_KEY=""
ENV PAPER_TRADING=True
ENV DB_PATH=/app/data/trades.db
ENV LOG_PATH=/app/data/engine.log
ENV API_HOST=0.0.0.0
ENV API_PORT=8000

# Volumen para persistir datos aunque el contenedor se reinicie
VOLUME ["/app/data"]

# Exponer puerto del API del dashboard
EXPOSE 8000

# Comando principal: arranca el motor de trading
CMD ["python", "main.py"]
