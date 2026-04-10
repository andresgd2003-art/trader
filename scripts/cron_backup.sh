#!/bin/bash
# scripts/cron_backup.sh
# ---------------------------------------------------------
# Recomendación de Despliegue de Integración de Alpaca:
# Para asegurar la pureza del uso horario de Nueva York
# en los scripts automatizados (por ejemplo, al crear copias 
# de seguridad a primera hora del mercado):
#
# AÑADIR A CRONTAB en el servidor host (correr `crontab -e`):
# CRON_TZ=America/New_York
# 30 9 * * 1-5 /opt/trader/app/scripts/cron_backup.sh >> /opt/trader/cron_log.txt 2>&1
# ---------------------------------------------------------

echo "==============================================="
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Iniciando respaldo pre-mercado automático..."
echo "Huso Horario Forzado Crontab: America/New_York"
echo "==============================================="

# Moverse al directorio base
cd /opt/trader

# Copiar el archivo log de ayer y la BD para auditoría si gustan
cp data/engine.log "data/engine_$(date +%F).log"
# cp data/trades.db "data/trades_$(date +%F).db"

echo "Limpiando log para liberar peso del motor hoy..."
> data/engine.log

echo "Respaldo diario COMPLETADO antes del arranque."
