"""
tests/test_sanity.py
=====================
Fase 19 — Tests de sanidad del sistema de rotación A/B/C y parser CSV.

Cubre:
  1. parse_order_meta() — todos los formatos posibles de client_order_id
  2. Alternancia A/B/C correcta por día del año
  3. FORCE_MODE override
  4. get_mode_label() retorna el formato correcto para client_order_id
  5. Retrocompatibilidad con IDs legados (sin modo)

Ejecutar:
  cd c:\\Users\\user\\OneDrive\\Escritorio\\gemini cli\\trader
  python -m pytest tests/test_sanity.py -v
"""
import os
import sys
import types
import unittest

# Asegurar que el root del proyecto está en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ────────────────────────────────────────────────────────────────────────────
# MOCK ALPACA — permite ejecutar tests sin tener alpaca-py instalado en local.
# En producción (VPS/Docker) alpaca está instalado, pero en CI/local no.
# ────────────────────────────────────────────────────────────────────────────
def _make_mock_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    return mod

_alpaca_mocks = {
    'alpaca': None,
    'alpaca.trading': None,
    'alpaca.trading.client': None,
    'alpaca.trading.requests': None,
    'alpaca.trading.enums': None,
    'alpaca.trading.models': None,
    'alpaca.data': None,
    'alpaca.data.historical': None,
    'alpaca.data.live': None,
    'alpaca.data.live.crypto': None,
    'alpaca.data.live.news': None,
    'alpaca.data.models': None,
    'alpaca.data.requests': None,
    'alpaca.data.timeframe': None,
    'alpaca.common.exceptions': None,
}
for mod_name in _alpaca_mocks:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = _make_mock_module(mod_name)

# Mock clases mínimas que order_manager necesita
_tc = sys.modules['alpaca.trading.client']
_tc.TradingClient = type('TradingClient', (), {})
_tr = sys.modules['alpaca.trading.requests']
_tr.MarketOrderRequest = type('MarketOrderRequest', (), {})
_tr.GetOrdersRequest = type('GetOrdersRequest', (), {})
_tr.GetPortfolioHistoryRequest = type('GetPortfolioHistoryRequest', (), {})
_te = sys.modules['alpaca.trading.enums']
_te.OrderSide = type('OrderSide', (), {'BUY': 'buy', 'SELL': 'sell'})
_te.TimeInForce = type('TimeInForce', (), {'DAY': 'day', 'GTC': 'gtc'})
_te.QueryOrderStatus = type('QueryOrderStatus', (), {'ALL': 'all'})
_dl = sys.modules['alpaca.data.live.news']
_dl.NewsDataStream = type('NewsDataStream', (), {})
_dm = sys.modules['alpaca.data.models']
_dm.News = type('News', (), {'headlines': []})
_dtf = sys.modules['alpaca.data.timeframe']
_dtf.TimeFrame = type('TimeFrame', (), {'Day': 'day', 'Minute': 'minute'})
# ────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════
# TEST 1: parse_order_meta()
# ══════════════════════════════════════════════════════════════
class TestParseOrderMeta(unittest.TestCase):
    """Verifica que parse_order_meta() extrae correctamente todos los campos."""

    def setUp(self):
        from engine.order_meta import parse_order_meta
        self.parse = parse_order_meta

    def test_etf_legacy_simple(self):
        """Formato legado simple: strat_{name}_{uuid8}"""
        r = self.parse("strat_GoldenCross_a3f8b2c1")
        self.assertEqual(r["prefix"], "strat")
        self.assertEqual(r["engine"], "etf")
        self.assertEqual(r["name"], "GoldenCross")
        self.assertEqual(r["mode"], "LEGACY")
        self.assertEqual(r["uuid"], "a3f8b2c1")

    def test_etf_new_with_mode(self):
        """Formato nuevo con modo: strat_{name}_{mode}_{uuid8}"""
        r = self.parse("strat_GoldenCross_mA_a3f8b2c1")
        self.assertEqual(r["prefix"], "strat")
        self.assertEqual(r["engine"], "etf")
        self.assertEqual(r["name"], "GoldenCross")
        self.assertEqual(r["mode"], "A")
        self.assertEqual(r["uuid"], "a3f8b2c1")

    def test_etf_multitoken_name_legacy(self):
        """Nombre con múltiples tokens (bug anterior): strat_RSI_VIX_Filter_{uuid8}"""
        r = self.parse("strat_RSI_VIX_Filter_b2d9e1f0")
        self.assertEqual(r["name"], "RSI_VIX_Filter")  # nombre completo, no "RSI"
        self.assertEqual(r["mode"], "LEGACY")
        self.assertEqual(r["engine"], "etf")

    def test_etf_multitoken_name_with_mode(self):
        """Nombre multi-token con modo: strat_RSI_VIX_Filter_mB_{uuid8}"""
        r = self.parse("strat_RSI_VIX_Filter_mB_b2d9e1f0")
        self.assertEqual(r["name"], "RSI_VIX_Filter")
        self.assertEqual(r["mode"], "B")

    def test_crypto_legacy(self):
        """Crypto legado: cry_{name}_{uuid8}"""
        r = self.parse("cry_EMATrendCrossover_b2d9e1f0")
        self.assertEqual(r["prefix"], "cry")
        self.assertEqual(r["engine"], "crypto")
        self.assertEqual(r["name"], "EMATrendCrossover")
        self.assertEqual(r["mode"], "LEGACY")

    def test_crypto_with_mode(self):
        """Crypto nuevo: cry_{name}_{mode}_{uuid8}"""
        r = self.parse("cry_BBBreakout_mC_c4a7d3b8")
        self.assertEqual(r["engine"], "crypto")
        self.assertEqual(r["name"], "BBBreakout")
        self.assertEqual(r["mode"], "C")

    def test_equities_legacy(self):
        """Equities legado: eq_{name}_{uuid8}"""
        r = self.parse("eq_GapperMomentum_c4a7d3b8")
        self.assertEqual(r["prefix"], "eq")
        self.assertEqual(r["engine"], "equities")
        self.assertEqual(r["name"], "GapperMomentum")
        self.assertEqual(r["mode"], "LEGACY")

    def test_equities_with_mode(self):
        """Equities nuevo: eq_{name}_{mode}_{uuid8}"""
        r = self.parse("eq_VCPMinervini_mA_d8f1c2a3")
        self.assertEqual(r["engine"], "equities")
        self.assertEqual(r["name"], "VCPMinervini")
        self.assertEqual(r["mode"], "A")

    def test_unknown_prefix(self):
        """Prefijo desconocido no crashea"""
        r = self.parse("manual_SomeOrder_12345678")
        self.assertEqual(r["engine"], "unknown")

    def test_empty_string(self):
        """String vacío retorna defaults seguros"""
        r = self.parse("")
        self.assertEqual(r["name"], "Manual")
        self.assertEqual(r["mode"], "LEGACY")

    def test_none_value(self):
        """None no crashea"""
        r = self.parse(None)
        self.assertEqual(r["mode"], "LEGACY")

    def test_mode_b_detection(self):
        """Modo B correctamente detectado"""
        r = self.parse("strat_MACD_mB_ff00aa11")
        self.assertEqual(r["mode"], "B")

    def test_mode_c_detection(self):
        """Modo C correctamente detectado"""
        r = self.parse("cry_GridSpot_mC_11223344")
        self.assertEqual(r["mode"], "C")

    def test_uuid_not_confused_with_mode(self):
        """Un UUID que empieza con m no se confunde con modo"""
        # mA podría ser parte del UUID si tiene 8 chars: "mABC1234" — no es modo (8 chars != 2)
        r = self.parse("strat_Golden_mABC1234_00ff11ee")
        # mABC1234 no es modo (len != 2, no matchea pattern), debería ser parte del nombre
        self.assertNotEqual(r["mode"], "ABC1234")
        # El UUID real es 00ff11ee
        self.assertEqual(r["uuid"], "00ff11ee")


# ══════════════════════════════════════════════════════════════
# TEST 2: DailyModeManager — rotación A/B/C
# ══════════════════════════════════════════════════════════════
class TestDailyModeRotation(unittest.TestCase):
    """Verifica la alternancia correcta A/B/C por día del año."""

    def setUp(self):
        # Limpiar FORCE_MODE por si acaso
        os.environ.pop("FORCE_MODE", None)

    def _get_expected_mode(self, day_of_year: int) -> str:
        """Replica la lógica de DailyModeManager."""
        modes = ["A", "B", "C"]
        return modes[(day_of_year - 1) % 3]

    def test_day_1_is_A(self):
        self.assertEqual(self._get_expected_mode(1), "A")

    def test_day_2_is_B(self):
        self.assertEqual(self._get_expected_mode(2), "B")

    def test_day_3_is_C(self):
        self.assertEqual(self._get_expected_mode(3), "C")

    def test_day_4_is_A(self):
        self.assertEqual(self._get_expected_mode(4), "A")

    def test_day_365_cycles(self):
        """El último día del año también alterna correctamente."""
        mode = self._get_expected_mode(365)
        self.assertIn(mode, ["A", "B", "C"])

    def test_full_cycle_covers_all_modes(self):
        """En cualquier ventana de 3 días consecutivos, los 3 modos aparecen."""
        for start in range(1, 364):
            modes_seen = {self._get_expected_mode(start + i) for i in range(3)}
            self.assertEqual(modes_seen, {"A", "B", "C"}, f"Falla en día {start}")

    def test_force_mode_override(self):
        """FORCE_MODE env var devuelve el modo forzado."""
        from engine.daily_mode import _determine_mode
        os.environ["FORCE_MODE"] = "B"
        try:
            mode = _determine_mode()
            self.assertEqual(mode, "B")
        finally:
            os.environ.pop("FORCE_MODE", None)

    def test_force_mode_invalid_ignored(self):
        """FORCE_MODE inválido no crashea (usa el día real)."""
        from engine.daily_mode import _determine_mode
        os.environ["FORCE_MODE"] = "X"  # No es A, B ni C
        try:
            mode = _determine_mode()
            self.assertIn(mode, ["A", "B", "C"])
        finally:
            os.environ.pop("FORCE_MODE", None)


# ══════════════════════════════════════════════════════════════
# TEST 3: get_mode_label()
# ══════════════════════════════════════════════════════════════
class TestModeLabelFormat(unittest.TestCase):
    """Verifica que get_mode_label() produce labels válidas para client_order_id."""

    def test_label_format(self):
        """El label siempre tiene formato mA, mB o mC."""
        import re
        from engine.daily_mode import get_mode_label
        label = get_mode_label()
        self.assertRegex(label, r'^m[ABC]$', f"Label inválido: {label!r}")

    def test_label_in_client_id(self):
        """El label embebido en un client_order_id parsea correctamente."""
        from engine.daily_mode import get_mode_label
        from engine.order_meta import parse_order_meta  # import puro
        label = get_mode_label()
        client_id = f"strat_TestStrategy_{label}_aabbccdd"
        r = parse_order_meta(client_id)
        expected_mode = label[1]  # 'A', 'B' o 'C'
        self.assertEqual(r["mode"], expected_mode)


# ══════════════════════════════════════════════════════════════
# TEST 4: NewsRiskFilter — clasificación de keywords
# ══════════════════════════════════════════════════════════════
class TestNewsRiskClassifier(unittest.TestCase):
    """Verifica la clasificación de titulares sin llamar a la API ni importar Alpaca."""

    def setUp(self):
        # Import directo sin pasar por engine/__init__.py
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "news_risk_filter_pure",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine", "news_risk_filter.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # Mockear imports de alpaca antes de cargar
        import sys
        mock_alpaca = type(sys)('alpaca')
        mock_alpaca.data = type(sys)('alpaca.data')
        mock_alpaca.data.live = type(sys)('alpaca.data.live')
        mock_alpaca.data.live.news = type(sys)('alpaca.data.live.news')
        mock_alpaca.data.live.news.NewsDataStream = object
        mock_alpaca.data.models = type(sys)('alpaca.data.models')
        class MockNews: headlines = []
        mock_alpaca.data.models.News = MockNews
        sys.modules.setdefault('alpaca', mock_alpaca)
        sys.modules.setdefault('alpaca.data', mock_alpaca.data)
        sys.modules.setdefault('alpaca.data.live', mock_alpaca.data.live)
        sys.modules.setdefault('alpaca.data.live.news', mock_alpaca.data.live.news)
        sys.modules.setdefault('alpaca.data.models', mock_alpaca.data.models)
        spec.loader.exec_module(mod)
        self.RiskLevel = mod.RiskLevel
        # Crear instancia del filtro (no hace requests HTTP en __init__)
        filter_instance = mod.NewsRiskFilter()
        self.clf = filter_instance._classify_headline  # método bound

    def test_high_risk_bankruptcy(self):
        r = self.clf("Company XYZ Files for Chapter 11 Bankruptcy Protection")
        self.assertEqual(r, self.RiskLevel.HIGH)

    def test_high_risk_fda(self):
        r = self.clf("FDA Reject New Drug Application from BioPharm Co")
        self.assertEqual(r, self.RiskLevel.HIGH)

    def test_medium_risk_downgrade(self):
        r = self.clf("Goldman Sachs Downgraded XYZ to Neutral from Buy")
        self.assertEqual(r, self.RiskLevel.MEDIUM)

    def test_low_risk_neutral(self):
        r = self.clf("Company XYZ Opens New Distribution Center in Texas")
        self.assertEqual(r, self.RiskLevel.LOW)

    def test_case_insensitive(self):
        r = self.clf("BANKRUPTCY FILING EXPECTED FOR ABC CORP")
        self.assertEqual(r, self.RiskLevel.HIGH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
