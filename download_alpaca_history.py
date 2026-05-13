import os
import csv
from datetime import datetime, timedelta
import alpaca_trade_api as tradeapi
from dotenv import load_dotenv

# Cargar variables de entorno (asegurate de tener un archivo .env con ALPACA_API_KEY y ALPACA_SECRET_KEY)
load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() in ("true", "1", "yes")

if not API_KEY or not SECRET_KEY:
    print("Error: No se encontraron las claves de API de Alpaca en las variables de entorno.")
    print("Por favor, crea un archivo .env con ALPACA_API_KEY y ALPACA_SECRET_KEY.")
    exit(1)

base_url = "https://paper-api.alpaca.markets" if PAPER_TRADING else "https://api.alpaca.markets"

print(f"Conectando a Alpaca (Paper Trading: {PAPER_TRADING})...")
api = tradeapi.REST(API_KEY, SECRET_KEY, base_url, api_version='v2')

try:
    account = api.get_account()
    print(f"Conexion exitosa. Cuenta de Alpaca ID: {account.id}")
except Exception as e:
    print(f"Error al conectar con la API de Alpaca: {e}")
    exit(1)

print("Descargando el historial completo de transacciones (órdenes cerradas)...")
print("Esto puede tardar un poco si hay muchas transacciones.")

# Descargar órdenes cerradas
# La API de Alpaca devuelve hasta 500 órdenes por request, así que se usa paginación si se necesita más
try:
    orders = api.list_orders(status="closed", limit=500, nested=True)
    all_orders = orders
    
    # Simple paginación basada en la fecha de la última orden si hay más (aunque list_orders no es la mejor para todo el historico, funciona para semanas/meses)
    while len(orders) == 500:
        last_date = orders[-1].created_at
        orders = api.list_orders(status="closed", limit=500, until=last_date.isoformat(), nested=True)
        # Evitar duplicar la última orden
        if orders and orders[0].id == all_orders[-1].id:
            orders = orders[1:]
        if not orders:
            break
        all_orders.extend(orders)

    print(f"Se descargaron {len(all_orders)} órdenes cerradas.")

    filename = "alpaca_history.csv"
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        # Escribir encabezados
        writer.writerow([
            "order_id", "client_order_id", "symbol", "asset_class", "qty", "filled_qty",
            "type", "side", "time_in_force", "limit_price", "stop_price",
            "filled_avg_price", "status", "created_at", "filled_at"
        ])
        
        # Escribir filas
        for o in all_orders:
            writer.writerow([
                o.id,
                o.client_order_id,
                o.symbol,
                o.asset_class,
                o.qty,
                o.filled_qty,
                o.order_type,
                o.side,
                o.time_in_force,
                o.limit_price,
                o.stop_price,
                o.filled_avg_price,
                o.status,
                o.created_at,
                o.filled_at
            ])
            
    print(f"Historial guardado exitosamente en '{filename}'.")
    print("Ya puedes usar este archivo para que la IA analice las ganancias de las estrategias.")
    
except Exception as e:
    print(f"Ocurrió un error descargando las órdenes: {e}")
