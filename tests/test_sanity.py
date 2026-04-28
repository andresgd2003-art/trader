"""
tests/test_sanity.py
=====================
Tests de sanidad del sistema: parser de órdenes y clasificador de noticias.

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
        """Formato: strat_{name}_{uuid8} → nombre mapeado a legible"""
        r = self.parse("strat_GoldenCross_a3f8b2c1")
        self.assertEqual(r["prefix"], "strat")
        self.assertEqual(r["engine"], "etf")
        self.assertEqual(r["name"], "Golden Cross")
        self.assertEqual(r["uuid"], "a3f8b2c1")

    def test_etf_old_with_mode(self):
        """Backward compat: IDs viejos con mA/mB/mC siguen parseando → nombre mapeado"""
        r = self.parse("strat_GoldenCross_mA_a3f8b2c1")
        self.assertEqual(r["prefix"], "strat")
        self.assertEqual(r["engine"], "etf")
        self.assertEqual(r["name"], "Golden Cross")
        self.assertEqual(r["uuid"], "a3f8b2c1")

    def test_etf_multitoken_name(self):
        """Nombre con múltiples tokens: strat_RSI_VIX_Filter_{uuid8}"""
        r = self.parse("strat_RSI_VIX_Filter_b2d9e1f0")
        self.assertEqual(r["name"], "RSI_VIX_Filter")
        self.assertEqual(r["engine"], "etf")

    def test_crypto_legacy(self):
        """Crypto: cry_{name}_{uuid8} → nombre mapeado a legible"""
        r = self.parse("cry_EMATrendCrossover_b2d9e1f0")
        self.assertEqual(r["prefix"], "cry")
        self.assertEqual(r["engine"], "crypto")
        self.assertEqual(r["name"], "EMA Trend Crossover")

    def test_equities_legacy(self):
        """Equities: eq_{name}_{uuid8}"""
        r = self.parse("eq_GapperMomentum_c4a7d3b8")
        self.assertEqual(r["prefix"], "eq")
        self.assertEqual(r["engine"], "equities")
        self.assertEqual(r["name"], "GapperMomentum")

    def test_unknown_prefix(self):
        """Prefijo desconocido no crashea"""
        r = self.parse("manual_SomeOrder_12345678")
        self.assertEqual(r["engine"], "unknown")

    def test_empty_string(self):
        """String vacío retorna defaults seguros"""
        r = self.parse("")
        self.assertEqual(r["name"], "Manual")

    def test_none_value(self):
        """None no crashea"""
        r = self.parse(None)
        self.assertIsNotNone(r)

    def test_uuid_not_confused_with_mode(self):
        """Un UUID que empieza con m no se confunde con modo"""
        r = self.parse("strat_Golden_mABC1234_00ff11ee")
        self.assertEqual(r["uuid"], "00ff11ee")


# ══════════════════════════════════════════════════════════════
# TEST 2: NewsRiskFilter — clasificación de keywords
# ══════════════════════════════════════════════════════════════
class TestNewsRiskClassifier(unittest.TestCase):
    """Verifica la clasificación de titulares sin llamar a la API ni importar Alpaca."""

    def setUp(self):
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "news_risk_filter_pure",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine", "news_risk_filter.py")
        )
        mod = importlib.util.module_from_spec(spec)
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
        filter_instance = mod.NewsRiskFilter()
        self.clf = filter_instance._classify_headline

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
