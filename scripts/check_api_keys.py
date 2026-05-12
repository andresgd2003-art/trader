"""
scripts/check_api_keys.py — Verificador de API Keys
=====================================================
Ejecuta este script para confirmar que las keys de Alpaca están
cargadas correctamente en todos los módulos del sistema.

Uso:
    python scripts/check_api_keys.py

Salida esperada: ✅ en cada módulo si todo está bien.
"""
import os
import sys

# Agregar directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cargar .env
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)

print("=" * 60)
print("  VERIFICADOR DE API KEYS — AlpacaNode Trading Engine")
print("=" * 60)

# 1. Variables de entorno
apca_key = os.environ.get("APCA_API_KEY_ID", "")
alpaca_key = os.environ.get("ALPACA_API_KEY", "")
apca_secret = os.environ.get("APCA_API_SECRET_KEY", "")
alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "")
paper = os.environ.get("PAPER_TRADING", "True")

effective_key = apca_key or alpaca_key
effective_secret = apca_secret or alpaca_secret

print("\n[1] Variables de entorno:")
print(f"  APCA_API_KEY_ID    = {'SET (' + apca_key[:4] + '***)' if apca_key else 'NOT SET'}")
print(f"  ALPACA_API_KEY     = {'SET (' + alpaca_key[:4] + '***)' if alpaca_key else 'NOT SET'}")
print(f"  APCA_API_SECRET    = {'SET' if apca_secret else 'NOT SET'}")
print(f"  ALPACA_SECRET_KEY  = {'SET' if alpaca_secret else 'NOT SET'}")
print(f"  PAPER_TRADING      = {paper}")

if not effective_key or not effective_secret:
    print("\n❌ CRÍTICO: No se encontraron API keys. Verifica tu archivo .env")
    sys.exit(1)

# Detectar modo
is_paper_env = paper.lower() == "true"
is_paper_key = effective_key.startswith("PK")
print(f"\n  → Key efectiva: {effective_key[:4]}*** (prefijo={'PK=PAPER' if is_paper_key else 'AK=LIVE'})")
print(f"  → PAPER_TRADING env: {is_paper_env}")

if is_paper_key and not is_paper_env:
    print("  ⚠️  ADVERTENCIA: Key es PAPER (PK...) pero PAPER_TRADING=False. Inconsistencia.")
elif not is_paper_key and is_paper_env:
    print("  ⚠️  ADVERTENCIA: Key es LIVE (AK...) pero PAPER_TRADING=True. Inconsistencia.")
else:
    print("  ✅ Key y PAPER_TRADING son consistentes.")

# 2. Conexión real a Alpaca
print("\n[2] Conexión a Alpaca Trading API:")
try:
    from alpaca.trading.client import TradingClient
    is_paper_mode = is_paper_env
    client = TradingClient(api_key=effective_key, secret_key=effective_secret, paper=is_paper_mode)
    account = client.get_account()
    print(f"  ✅ Conexión exitosa. Modo: {'PAPER' if is_paper_mode else 'LIVE'}")
    print(f"     Equity: ${float(account.equity):,.2f}")
    print(f"     Cash:   ${float(account.cash):,.2f}")
    settled = float(getattr(account, 'settled_cash', 0) or 0)
    print(f"     Settled Cash: ${settled:,.2f}")
    bp = float(account.buying_power or 0)
    print(f"     Buying Power: ${bp:,.2f}")
    if settled < 10:
        print(f"     ⚠️  settled_cash=${settled:.2f} muy bajo — trades ETF bloqueados (notional < $1)")
except Exception as e:
    print(f"  ❌ ERROR conectando: {e}")

# 3. Order Manager ETF
print("\n[3] OrderManager ETF (strat_ prefix):")
try:
    from engine.order_manager import OrderManager
    om = OrderManager()
    acc = om.get_account()
    print(f"  ✅ Inicializado. paper={om.paper} | cash=${acc.get('cash', 0):,.2f}")
    if om.api_key != effective_key:
        print(f"  ⚠️  Key DIFERENTE a la efectiva. Revisión requerida.")
    else:
        print(f"  ✅ Usa la misma key ({om.api_key[:4]}***)")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

# 4. Order Manager Equities
print("\n[4] OrderManagerEquities (eq_ prefix):")
try:
    from engine.order_manager_equities import OrderManagerEquities
    ome = OrderManagerEquities()
    print(f"  ✅ Inicializado. paper={ome.paper}")
    if ome.api_key != effective_key:
        print(f"  ⚠️  Key DIFERENTE a la efectiva.")
    else:
        print(f"  ✅ Usa la misma key ({ome.api_key[:4]}***)")
    # Verificar notional de equities
    notional = ome._calculate_notional()
    print(f"  → Notional calculado para equities: ${notional:.2f}")
    if notional <= 0:
        print(f"  ❌ Notional=$0 — compras BLOQUEADAS por reserva de capital o settled_cash vacío")
    elif notional < 5:
        print(f"  ⚠️  Notional muy bajo (${notional:.2f}) — posible problema de capital")
    else:
        print(f"  ✅ Notional OK (${notional:.2f})")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

# 5. Order Manager Crypto
print("\n[5] OrderManagerCrypto (cry_ prefix):")
try:
    from engine.order_manager_crypto import OrderManagerCrypto
    omc = OrderManagerCrypto()
    print(f"  ✅ Inicializado. paper={omc.paper}")
    if omc.api_key != effective_key:
        print(f"  ⚠️  Key DIFERENTE a la efectiva.")
    else:
        print(f"  ✅ Usa la misma key ({omc.api_key[:4]}***)")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

# 6. Regime Manager
print("\n[6] RegimeManager:")
try:
    from engine.regime_manager import RegimeManager, get_current_regime
    rm = RegimeManager()
    if rm.api_key != effective_key:
        print(f"  ⚠️  Key DIFERENTE a la efectiva.")
    else:
        print(f"  ✅ Usa la misma key ({rm.api_key[:4]}***)")
    regime = rm.assess()
    state = get_current_regime()
    print(f"  ✅ Régimen actual: {state.get('regime')} | SPY={state.get('spy_price')} | VIX~{state.get('vix_price')}")
    print(f"     suggested_sizing={state.get('suggested_sizing')}")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

# 7. Verificar posiciones actuales
print("\n[7] Posiciones abiertas:")
try:
    from alpaca.trading.client import TradingClient
    client = TradingClient(api_key=effective_key, secret_key=effective_secret, paper=is_paper_env)
    positions = client.get_all_positions()
    if not positions:
        print("  (Sin posiciones abiertas)")
    for p in positions:
        print(f"  {p.symbol}: qty={p.qty} | unrealized_pl=${float(p.unrealized_pl or 0):+.2f}")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

# 8. Verificar capital disponible para ETF (simulación de notional)
print("\n[8] Simulación de notional ETF (RSIDipStrategy - TQQQ):")
try:
    from alpaca.trading.client import TradingClient
    from engine.regime_manager import get_current_regime
    client = TradingClient(api_key=effective_key, secret_key=effective_secret, paper=is_paper_env)
    account = client.get_account()
    settled_cash = float(getattr(account, 'settled_cash', None) or account.cash)
    regime_data = get_current_regime()
    regime_str = regime_data.get("regime", "UNKNOWN")
    PCT = {"BULL": 0.08, "CHOP": 0.05, "BEAR": 0.03, "UNKNOWN": 0.03}
    pct = PCT.get(regime_str, 0.02)
    dynamic_notional = round(settled_cash * pct, 2)
    print(f"  settled_cash=${settled_cash:.2f} | régimen={regime_str} | pct={pct*100:.0f}%")
    print(f"  → Notional calculado para ETF (TQQQ): ${dynamic_notional:.2f}")
    if dynamic_notional < 1.0:
        print(f"  ❌ Notional < $1 → Las compras ETF son BLOQUEADAS. ESTO explica por qué TQQQ no se compra.")
        print(f"     Solución: aumentar settled_cash o ajustar el porcentaje mínimo.")
    else:
        print(f"  ✅ Notional suficiente para ejecutar órdenes ETF.")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

print("\n" + "=" * 60)
print("  Diagnóstico completado.")
print("=" * 60)
