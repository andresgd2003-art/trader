"""
Script manual para auditar y liquidar posiciones huérfanas.
PASO 1: Listar todas las posiciones abiertas para decidir cuáles son huerfanas.
PASO 2: Liquidar las identificadas como huérfanas.

Ejecutar desde el VPS o local: python3 liquidar_huerfanas.py
"""
import os
import sys
from dotenv import load_dotenv

# Cargar .env (funciona en VPS /opt/trader/.env y local)
for env_path in ['/opt/trader/.env', '.env']:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

api_key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
secret_key = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
paper = os.environ.get("PAPER_TRADING", "True").lower() == "true"

client = TradingClient(api_key, secret_key, paper=paper)

# ─── PASO 1: Listar todas las posiciones abiertas ───
print("=" * 70)
print("  AUDITORÍA DE POSICIONES ABIERTAS")
print("=" * 70)

positions = client.get_all_positions()
if not positions:
    print("✅ No hay posiciones abiertas. Nada que liquidar.")
    sys.exit(0)

for p in positions:
    qty = float(p.qty)
    market_value = float(p.market_value)
    avg_entry = float(p.avg_entry_price)
    current = float(p.current_price)
    unrealized_pl = float(p.unrealized_pl)
    unrealized_plpc = float(p.unrealized_plpc) * 100
    asset_class = getattr(p, 'asset_class', 'unknown')
    
    print(f"\n{'─' * 50}")
    print(f"  {p.symbol}  ({asset_class})")
    print(f"  Qty: {qty}  │  Avg Entry: ${avg_entry:.4f}")
    print(f"  Current: ${current:.4f}  │  Market Value: ${market_value:.2f}")
    print(f"  P&L: ${unrealized_pl:.2f} ({unrealized_plpc:+.2f}%)")

print(f"\n{'=' * 70}")
print(f"  Total posiciones: {len(positions)}")
print(f"{'=' * 70}")

# ─── PASO 2: LIQUIDACIÓN (descomentear para ejecutar) ───
# 
# Para liquidar posiciones específicas, descomentar y ajustar:
#
# HUERFANAS = [
#     # (symbol, qty_a_vender, "razon")
#     # Ejemplo: ("SPY", 16, "huérfana ETF sin estrategia activa"),
#     # Ejemplo: ("LINK/USD", 22.01, "huérfana crypto sin estrategia activa"), 
# ]
#
# print("\n⚠️  LIQUIDANDO POSICIONES HUÉRFANAS...")
# for sym, qty, reason in HUERFANAS:
#     is_crypto = "/" in sym
#     try:
#         req = MarketOrderRequest(
#             symbol=sym,
#             qty=qty,
#             side=OrderSide.SELL,
#             time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY,
#             client_order_id=f"{'cry' if is_crypto else 'etf'}_OrphanLiquidation_{sym.replace('/', '')[:8]}"
#         )
#         o = client.submit_order(req)
#         print(f"  ✅ Liquidada {qty} {sym}: order_id={o.id} ({reason})")
#     except Exception as e:
#         print(f"  ❌ Error liquidando {qty} {sym}: {e}")
