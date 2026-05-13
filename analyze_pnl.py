import pandas as pd

# Cargar los datos
df = pd.read_csv('reporte_all_all_weekly.csv')

# Asegurar que el P&L Realizado sea numérico (los '-' se vuelven NaN)
df['P&L Realizado ($)'] = pd.to_numeric(df['P&L Realizado ($)'], errors='coerce')

# Rellenar NaN con 0 para poder sumar
df['P&L Realizado ($)'].fillna(0, inplace=True)

# Agrupar por Estrategia y sumar el P&L
pnl_por_estrategia = df.groupby('Estrategia')['P&L Realizado ($)'].sum().reset_index()

# Ordenar de mayor a menor ganancia
pnl_por_estrategia = pnl_por_estrategia.sort_values(by='P&L Realizado ($)', ascending=False)

# Mostrar resultados
print("=== RANKING DE ESTRATEGIAS ===")
for index, row in pnl_por_estrategia.iterrows():
    estrategia = row['Estrategia']
    pnl = row['P&L Realizado ($)']
    print(f"{estrategia}: ${pnl:.2f}")

# Separar ganadoras y perdedoras
ganadoras = pnl_por_estrategia[pnl_por_estrategia['P&L Realizado ($)'] > 0]
perdedoras = pnl_por_estrategia[pnl_por_estrategia['P&L Realizado ($)'] <= 0]

print("\n--- Estrategias a ELIMINAR (PnL <= 0) ---")
for index, row in perdedoras.iterrows():
    print(f"- {row['Estrategia']}")
