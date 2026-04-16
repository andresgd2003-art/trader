"""
engine/logger.py
================
Sistema de logs con rotación automática de archivos.
Genera logs en formato JSON para que el dashboard los pueda leer fácilmente.
"""
import logging
import logging.handlers
import json
import os
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """
    Formateador que convierte cada log en una línea JSON.
    Ejemplo: {"time": "11:42:01", "level": "INFO", "source": "Engine", "msg": "..."}
    """
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "source": record.name.split(".")[-1],   # Solo el nombre del módulo
            "msg": record.getMessage(),
        }
        return json.dumps(log_entry)


def setup_logger(log_path: str = "/opt/trader/data/engine.log") -> logging.Logger:
    """
    Configura el logger principal del sistema.

    Args:
        log_path: Ruta donde se guardan los archivos de log

    Returns:
        Logger configurado listo para usar
    """
    # Crear directorio si no existe
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # --- Handler 1: Archivo con rotación automática ---
    # Máximo 10 MB por archivo, guarda los últimos 5 archivos
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(logging.INFO)

    # --- Handler 2: Consola (para ver en Docker logs) ---
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)

    # Evitar handlers duplicados si se llama varias veces
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger
