#!/bin/bash
# Script de aprovisionamiento de VPS Ubuntu para Proyecto Trader (Alpaca)

# 1. Actualizar sistema
sudo apt update && sudo apt upgrade -y

# 2. Instalar dependencias del sistema y chrony
sudo apt install -y python3-pip python3-venv chrony sqlite3

# 3. Configurar Chrony (Sincronización de Tiempo Estricta)
echo "server time.google.com iburst" | sudo tee /etc/chrony/chrony.conf
sudo systemctl restart chrony
sudo systemctl enable chrony

# 4. Crear entorno virtual estricto
cd /opt/trader
python3 -m venv /opt/trader/venv

# 5. Instalar dependencias críticas de Python
/opt/trader/venv/bin/pip install --upgrade pip
/opt/trader/venv/bin/pip install alpaca-py==0.32.0 fastapi==0.104.1 uvicorn==0.24.0 pandas==2.1.3

echo "Configuración de sistema operativo (FASE 1) completada."
