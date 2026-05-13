"""
tests/test_regime_v2.py
========================
Tests TDD para Propuesta 2 (ADX + VIX structure) y Propuesta 3 (Confidence Score).
Deben FALLAR antes de implementar y PASAR después.
"""
import sys, os, types, unittest
from unittest.mock import patch, MagicMock

# Pre-mock yfinance y requests (sin red en tests)
_mock_yf = types.ModuleType('yfinance')
_mock_yf.Ticker = MagicMock()
sys.modules.setdefault('yfinance', _mock_yf)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.regime_manager import RegimeManager, Regime


# ══════════════════════════════════════════════════════════════════
# GRUPO A — Lógica de _compute_confidence_score (ETF/Equities)
# ══════════════════════════════════════════════════════════════════
class TestConfidenceScoreETF(unittest.TestCase):

    def setUp(self):
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()

    def test_method_exists(self):
        """_compute_confidence_score debe existir en RegimeManager."""
        self.assertTrue(hasattr(self.rm, '_compute_confidence_score'),
            "_compute_confidence_score no existe — implementar primero")

    def test_bull_high_adx_contango_above_75(self):
        """BULL + ADX fuerte + contango profundo → confianza >= 0.75."""
        score = self.rm._compute_confidence_score(
            regime=Regime.BULL, adx_value=38.0, vix_ratio=0.78,
            spy_price=750.0, spy_sma20=735.0, spy_sma50=710.0
        )
        self.assertGreaterEqual(score, 0.75,
            f"BULL confirmado debería dar score >= 0.75, got {score}")

    def test_bull_low_adx_backwardation_below_40(self):
        """BULL + ADX débil + backwardation → confianza baja <= 0.45."""
        score = self.rm._compute_confidence_score(
            regime=Regime.BULL, adx_value=13.0, vix_ratio=1.15,
            spy_price=750.0, spy_sma20=748.0, spy_sma50=745.0
        )
        self.assertLessEqual(score, 0.45,
            f"BULL sin confirmar debería dar score <= 0.45, got {score}")

    def test_bear_strong_adx_backwardation_above_70(self):
        """BEAR + ADX fuerte + backwardation → confianza >= 0.70."""
        score = self.rm._compute_confidence_score(
            regime=Regime.BEAR, adx_value=32.0, vix_ratio=1.25,
            spy_price=680.0, spy_sma20=700.0, spy_sma50=720.0
        )
        self.assertGreaterEqual(score, 0.70,
            f"BEAR confirmado debería dar score >= 0.70, got {score}")

    def test_bear_weak_adx_contango_below_45(self):
        """BEAR + ADX débil + contango → confianza baja (falsa señal)."""
        score = self.rm._compute_confidence_score(
            regime=Regime.BEAR, adx_value=12.0, vix_ratio=0.85,
            spy_price=680.0, spy_sma20=700.0, spy_sma50=720.0
        )
        self.assertLessEqual(score, 0.45,
            f"BEAR sin confirmar debería dar score <= 0.45, got {score}")

    def test_chop_low_adx_neutral_ratio_above_60(self):
        """CHOP + ADX bajo + ratio neutral → CHOP genuino, score >= 0.60."""
        score = self.rm._compute_confidence_score(
            regime=Regime.CHOP, adx_value=15.0, vix_ratio=0.96,
            spy_price=720.0, spy_sma20=719.0, spy_sma50=715.0
        )
        self.assertGreaterEqual(score, 0.60,
            f"CHOP confirmado debería dar score >= 0.60, got {score}")

    def test_chop_high_adx_transition_below_40(self):
        """CHOP + ADX alto → mercado en transición, no lateral genuino."""
        score = self.rm._compute_confidence_score(
            regime=Regime.CHOP, adx_value=34.0, vix_ratio=1.02,
            spy_price=720.0, spy_sma20=718.0, spy_sma50=715.0
        )
        self.assertLessEqual(score, 0.45,
            f"CHOP con ADX alto debería dar score <= 0.45, got {score}")

    def test_score_never_below_015(self):
        """El score nunca debe ser 0 — mínimo 0.15 para no bloquear totalmente."""
        score = self.rm._compute_confidence_score(
            regime=Regime.BULL, adx_value=0.0, vix_ratio=2.0,
            spy_price=700.0, spy_sma20=750.0, spy_sma50=780.0
        )
        self.assertGreaterEqual(score, 0.15, f"Score mínimo debe ser 0.15, got {score}")

    def test_score_never_above_one(self):
        """El score nunca debe superar 1.0."""
        score = self.rm._compute_confidence_score(
            regime=Regime.BULL, adx_value=50.0, vix_ratio=0.5,
            spy_price=780.0, spy_sma20=750.0, spy_sma50=710.0
        )
        self.assertLessEqual(score, 1.0, f"Score máximo debe ser 1.0, got {score}")

    def test_score_is_float(self):
        """El score debe ser un número flotante."""
        score = self.rm._compute_confidence_score(
            regime=Regime.CHOP, adx_value=20.0, vix_ratio=1.0,
            spy_price=720.0, spy_sma20=718.0, spy_sma50=715.0
        )
        self.assertIsInstance(score, float, f"Score debe ser float, got {type(score)}")


# ══════════════════════════════════════════════════════════════════
# GRUPO B — Extracción de ADX en assess()
# ══════════════════════════════════════════════════════════════════
class TestADXExtraction(unittest.TestCase):

    def setUp(self):
        import numpy as np, pandas as pd
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()
        # DataFrame con 60 barras diarias incluyendo high, low, close
        n = 60
        closes = list(np.linspace(700, 750, n))
        highs  = [c + 3.0 for c in closes]
        lows   = [c - 3.0 for c in closes]
        self.spy_df = pd.DataFrame({
            'symbol': ['SPY'] * n,
            'close':  closes,
            'high':   highs,
            'low':    lows,
            'timestamp': pd.date_range('2026-01-01', periods=n, freq='D')
        })

    def _make_mock_bars(self, df):
        mock_bars = MagicMock()
        mock_bars.df = df.set_index(['symbol', 'timestamp'])
        return mock_bars

    def test_adx_value_in_current_regime_after_assess(self):
        """Después de assess(), _CURRENT_REGIME debe tener 'adx_value'."""
        from engine.regime_manager import _CURRENT_REGIME
        mock_bars = self._make_mock_bars(self.spy_df)
        self.rm.client.get_stock_bars = MagicMock(return_value=mock_bars)

        mock_yf_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=15.0))
        ))
        mock_yf_ticker.return_value.history.return_value = mock_hist

        with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf_ticker)}):
            self.rm.assess()

        self.assertIn('adx_value', _CURRENT_REGIME,
            "adx_value debe estar en _CURRENT_REGIME tras assess()")
        self.assertIsNotNone(_CURRENT_REGIME['adx_value'],
            "adx_value no debe ser None")

    def test_adx_value_is_positive(self):
        """El ADX debe ser un número positivo."""
        from engine.regime_manager import _CURRENT_REGIME
        mock_bars = self._make_mock_bars(self.spy_df)
        self.rm.client.get_stock_bars = MagicMock(return_value=mock_bars)

        mock_yf_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=15.0))
        ))
        mock_yf_ticker.return_value.history.return_value = mock_hist

        with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf_ticker)}):
            self.rm.assess()

        adx = _CURRENT_REGIME.get('adx_value', -1)
        self.assertGreater(adx, 0, f"ADX debe ser positivo, got {adx}")

    def test_assess_handles_missing_high_column_gracefully(self):
        """Si el DataFrame no tiene 'high'/'low', no debe crashear."""
        import pandas as pd, numpy as np
        n = 60
        df_no_hl = pd.DataFrame({
            'symbol': ['SPY'] * n,
            'close': list(np.linspace(700, 750, n)),
            'timestamp': pd.date_range('2026-01-01', periods=n, freq='D')
        })
        mock_bars = MagicMock()
        mock_bars.df = df_no_hl.set_index(['symbol', 'timestamp'])
        self.rm.client.get_stock_bars = MagicMock(return_value=mock_bars)

        mock_yf_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=15.0))
        ))
        mock_yf_ticker.return_value.history.return_value = mock_hist

        try:
            with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf_ticker)}):
                self.rm.assess()
        except Exception as e:
            self.fail(f"assess() no debe crashear sin columnas high/low: {e}")

    def test_confidence_score_in_current_regime(self):
        """_CURRENT_REGIME debe tener 'confidence_score' tras assess()."""
        from engine.regime_manager import _CURRENT_REGIME
        mock_bars = self._make_mock_bars(self.spy_df)
        self.rm.client.get_stock_bars = MagicMock(return_value=mock_bars)

        mock_yf_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=15.0))
        ))
        mock_yf_ticker.return_value.history.return_value = mock_hist

        with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf_ticker)}):
            self.rm.assess()

        self.assertIn('confidence_score', _CURRENT_REGIME,
            "confidence_score debe estar en _CURRENT_REGIME")
        score = _CURRENT_REGIME['confidence_score']
        self.assertGreaterEqual(score, 0.15)
        self.assertLessEqual(score, 1.0)


# ══════════════════════════════════════════════════════════════════
# GRUPO C — Estructura del VIX (ETF/Equities) y F&G (Crypto)
# ══════════════════════════════════════════════════════════════════
class TestVIXStructure(unittest.TestCase):

    def setUp(self):
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()

    def test_vix_ratio_contango_below_one(self):
        """VIX9D=14, VIX3M=18 → ratio=0.78 → contango."""
        score = self.rm._compute_confidence_score(
            regime=Regime.BULL, adx_value=28.0, vix_ratio=0.78,
            spy_price=750.0, spy_sma20=735.0, spy_sma50=710.0
        )
        # Contango profundo en BULL → boost de confianza
        self.assertGreaterEqual(score, 0.70)

    def test_vix_ratio_backwardation_above_one(self):
        """VIX9D=22, VIX3M=18 → ratio=1.22 → backwardation reduce confianza en BULL."""
        score_contango = self.rm._compute_confidence_score(
            regime=Regime.BULL, adx_value=28.0, vix_ratio=0.78,
            spy_price=750.0, spy_sma20=735.0, spy_sma50=710.0
        )
        score_backw = self.rm._compute_confidence_score(
            regime=Regime.BULL, adx_value=28.0, vix_ratio=1.22,
            spy_price=750.0, spy_sma20=735.0, spy_sma50=710.0
        )
        self.assertGreater(score_contango, score_backw,
            "Contango debe dar mayor confianza que backwardation en BULL")

    def test_vix_ratio_zero_does_not_crash(self):
        """VIX3M=0 no debe causar ZeroDivisionError."""
        try:
            score = self.rm._compute_confidence_score(
                regime=Regime.CHOP, adx_value=20.0, vix_ratio=1.0,
                spy_price=720.0, spy_sma20=718.0, spy_sma50=715.0
            )
        except ZeroDivisionError:
            self.fail("ZeroDivisionError cuando vix_ratio=1.0 (VIX3M=0 fallback)")

    def test_vix_structure_field_in_current_regime(self):
        """'vix_structure' debe aparecer en _CURRENT_REGIME tras assess()."""
        from engine.regime_manager import _CURRENT_REGIME
        import numpy as np, pandas as pd
        n = 60
        closes = list(np.linspace(700, 750, n))
        spy_df = pd.DataFrame({
            'symbol': ['SPY'] * n, 'close': closes,
            'high': [c+3 for c in closes], 'low': [c-3 for c in closes],
            'timestamp': pd.date_range('2026-01-01', periods=n, freq='D')
        })
        mock_bars = MagicMock()
        mock_bars.df = spy_df.set_index(['symbol', 'timestamp'])
        self.rm.client.get_stock_bars = MagicMock(return_value=mock_bars)

        mock_yf = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=15.0))
        ))
        mock_yf.return_value.history.return_value = mock_hist

        with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf)}):
            self.rm.assess()

        self.assertIn('vix_structure', _CURRENT_REGIME,
            "'vix_structure' debe estar en _CURRENT_REGIME")
        self.assertIn(_CURRENT_REGIME.get('vix_structure', ''),
            ['contango', 'backwardation', 'unknown'])


# ══════════════════════════════════════════════════════════════════
# GRUPO D — Fear & Greed para Crypto Confidence Score
# ══════════════════════════════════════════════════════════════════
class TestFearGreedCrypto(unittest.TestCase):

    def setUp(self):
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()

    def test_method_exists(self):
        """_compute_crypto_confidence debe existir."""
        self.assertTrue(hasattr(self.rm, '_compute_crypto_confidence'),
            "_compute_crypto_confidence no existe — implementar")

    def test_extreme_greed_bull_high_score(self):
        """F&G=80 (Extreme Greed) en BULL → alta confianza."""
        score = self.rm._compute_crypto_confidence(
            regime=Regime.BULL, fg_value=80
        )
        self.assertGreaterEqual(score, 0.70)

    def test_extreme_fear_bear_high_score(self):
        """F&G=15 (Extreme Fear) en BEAR → confianza alta (confirmado)."""
        score = self.rm._compute_crypto_confidence(
            regime=Regime.BEAR, fg_value=15
        )
        self.assertGreaterEqual(score, 0.70)

    def test_extreme_greed_bear_low_score(self):
        """F&G=80 en BEAR → sentimiento contradice régimen, baja confianza."""
        score = self.rm._compute_crypto_confidence(
            regime=Regime.BEAR, fg_value=80
        )
        self.assertLessEqual(score, 0.45)

    def test_fg_none_fallback_neutral(self):
        """F&G=None → fallback a 0.5 (neutral, no bloquear)."""
        score = self.rm._compute_crypto_confidence(
            regime=Regime.BULL, fg_value=None
        )
        self.assertEqual(score, 0.5, "F&G None debe retornar 0.5")

    def test_score_clamp_min(self):
        """Crypto confidence mínimo 0.15."""
        score = self.rm._compute_crypto_confidence(
            regime=Regime.BULL, fg_value=10  # Extreme Fear en BULL = bajo
        )
        self.assertGreaterEqual(score, 0.15)

    def test_crypto_confidence_in_current_regime(self):
        """'crypto_confidence_score' debe estar en _CURRENT_REGIME."""
        from engine.regime_manager import _CURRENT_REGIME
        import numpy as np, pandas as pd
        n = 60
        closes = list(np.linspace(700, 750, n))
        spy_df = pd.DataFrame({
            'symbol': ['SPY'] * n, 'close': closes,
            'high': [c+3 for c in closes], 'low': [c-3 for c in closes],
            'timestamp': pd.date_range('2026-01-01', periods=n, freq='D')
        })
        mock_bars = MagicMock()
        mock_bars.df = spy_df.set_index(['symbol', 'timestamp'])
        self.rm.client.get_stock_bars = MagicMock(return_value=mock_bars)

        mock_yf = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=15.0))
        ))
        mock_yf.return_value.history.return_value = mock_hist

        with patch('requests.get') as mock_req:
            mock_req.return_value.json.return_value = {
                'data': [{'value': '55', 'value_classification': 'Greed'}]
            }
            mock_req.return_value.raise_for_status = MagicMock()
            with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf)}):
                self.rm.assess()

        self.assertIn('crypto_confidence_score', _CURRENT_REGIME,
            "'crypto_confidence_score' debe estar en _CURRENT_REGIME")


if __name__ == '__main__':
    unittest.main(verbosity=2)
