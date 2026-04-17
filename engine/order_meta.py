"""
engine/order_meta.py
=====================
Parser puro de client_order_id sin dependencias de Alpaca.
Se extrae aquí para poder importarse en tests sin necesitar las keys.

El api_server.py importa parse_order_meta desde aquí.
"""
import re
from typing import Optional

# Mapa de prefijo → nombre del motor
ENGINE_MAP = {
    "strat": "etf",
    "cry":   "crypto",
    "eq":    "equities",
}

_UUID8_PATTERN = re.compile(r'^[0-9a-f]{8}$', re.IGNORECASE)
_MODE_PATTERN  = re.compile(r'^m[ABC]$', re.IGNORECASE)


def parse_order_meta(raw: Optional[str]) -> dict:
    """
    Extrae prefix, engine, name, mode y uuid de un client_order_id.

    Formatos soportados:
      strat_{name}_{uuid8}                 → LEGACY, etf
      strat_{name}_{mA|mB|mC}_{uuid8}     → modo A/B/C, etf
      cry_{name}_{uuid8}                   → LEGACY, crypto
      cry_{name}_{mA|mB|mC}_{uuid8}        → modo A/B/C, crypto
      eq_{name}_{uuid8}                    → LEGACY, equities
      eq_{name}_{mA|mB|mC}_{uuid8}         → modo A/B/C, equities
      Manual_xxx                           → nombre manual, unknown
    """
    if not raw:
        return {"prefix": "unknown", "engine": "unknown", "name": "Manual", "mode": "LEGACY", "uuid": ""}

    parts = raw.split("_")

    if len(parts) < 2:
        return {"prefix": "unknown", "engine": "unknown", "name": raw, "mode": "LEGACY", "uuid": ""}

    prefix = parts[0]
    engine = ENGINE_MAP.get(prefix, "unknown")

    # Detectar si el último token es un UUID8
    last = parts[-1]
    if _UUID8_PATTERN.match(last):
        uuid_part = last
        inner = parts[1:-1]  # todo entre prefix y uuid
    else:
        uuid_part = ""
        inner = parts[1:]    # sin uuid

    # Detectar si el penúltimo token (antes del UUID) es un modo mA/mB/mC
    mode = "LEGACY"
    if inner and _MODE_PATTERN.match(inner[-1]):
        _ignored_mode = inner[-1][1].upper()  # backward compat: skip mode token
        name_parts = inner[:-1]
    else:
        name_parts = inner

    name = "_".join(name_parts) if name_parts else "unknown"

    return {
        "prefix": prefix,
        "engine": engine,
        "name":   name,
        "mode":   mode,
        "uuid":   uuid_part,
    }
