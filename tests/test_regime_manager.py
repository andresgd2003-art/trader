"""
tests/test_regime_manager.py
==============================
Tests unitarios para los 5 fixes de la auditoría del Regime Manager.

Fix 1: Barras diarias (no intradía)
Fix 2: Histéresis anti-whipsaw
Fix 3: No evaluar fuera de horario
Fix 4: VIXY fallback mejorado
Fix 5: Log muestra estrategias reales
"""
import sys
import os
import types
import unittest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

# Pre-mock yfinance before it's imported (broken locally due to missing websockets.sync)
_mock_yf_module = types.ModuleType('yfinance')
_mock_yf_module.Ticker = MagicMock()
sys.modules.setdefault('yfinance', _mock_yf_module)

# Agregar raíz del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alpaca.data.timeframe import TimeFrame
from engine.regime_manager import (
    RegimeManager, Regime, get_current_regime,
    REGIME_ETF_MAP, REGIME_CRYPTO_MAP, REGIME_EQUITIES_MAP,
    SIZING_BY_REGIME, _CURRENT_REGIME
)


class TestRegimeManagerHysteresis(unittest.TestCase):
    """Fix 2: Histéresis — el régimen debe tener umbrales separados para entrar/salir."""

    def setUp(self):
        """Crear RegimeManager mockeado (sin API real)."""
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()

    def test_hysteresis_thresholds_exist(self):
        """Verificar que existen umbrales separados entry/exit."""
        self.assertTrue(hasattr(self.rm, 'VIX_BULL_ENTRY'))
        self.assertTrue(hasattr(self.rm, 'VIX_BULL_EXIT'))
        self.assertTrue(hasattr(self.rm, 'VIX_BEAR_ENTRY'))
        self.assertTrue(hasattr(self.rm, 'VIX_BEAR_EXIT'))

    def test_bull_entry_stricter_than_exit(self):
        """Para entrar a BULL el VIX debe ser más bajo que para salir."""
        self.assertLess(self.rm.VIX_BULL_ENTRY, self.rm.VIX_BULL_EXIT)

    def test_bear_entry_stricter_than_exit(self):
        """Para entrar a BEAR el VIX debe ser más alto que para salir."""
        self.assertGreater(self.rm.VIX_BEAR_ENTRY, self.rm.VIX_BEAR_EXIT)

    def test_no_overlap_between_bull_and_bear(self):
        """Los rangos de histéresis de BULL y BEAR no deben solaparse."""
        self.assertLess(self.rm.VIX_BULL_EXIT, self.rm.VIX_BEAR_EXIT)

    def test_hysteresis_prevents_whipsaw(self):
        """Un VIX que fluctúa entre 17-19 NO debe causar cambio de régimen BULL→CHOP."""
        import numpy as np
        import pandas as pd

        with patch('engine.regime_manager.StockHistoricalDataClient'):
            rm = RegimeManager()

        # Simular datos diarios de SPY (alcista, por encima de SMA50)
        spy_prices = list(np.linspace(700, 740, 60))  # 60 días alcista
        spy_df = pd.DataFrame({
            'symbol': ['SPY'] * 60,
            'close': spy_prices,
            'timestamp': pd.date_range('2026-03-01', periods=60, freq='D')
        })

        mock_bars = MagicMock()
        mock_bars.df = spy_df.set_index(['symbol', 'timestamp'])

        def mock_get_bars(req):
            return mock_bars

        rm.client.get_stock_bars = mock_get_bars

        # Escenario: empezamos en BULL (VIX=15, SPY alcista)
        rm.current_regime = Regime.BULL

        # VIX sube a 17.5 (entre BULL_ENTRY=16 y BULL_EXIT=20)
        # Con histéresis, debe MANTENER BULL
        mock_yf_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=17.5))
        ))
        mock_yf_ticker.return_value.history.return_value = mock_hist

        with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf_ticker)}):
            result = rm.assess()
            self.assertEqual(result, Regime.BULL,
                "VIX=17.5 con histéresis NO debe sacar de BULL (exit threshold es 20)")

    def test_bull_exit_triggers_at_threshold(self):
        """VIX que sube a 21 (> BULL_EXIT=20) SÍ debe sacar de BULL."""
        import numpy as np
        import pandas as pd

        with patch('engine.regime_manager.StockHistoricalDataClient'):
            rm = RegimeManager()

        spy_prices = list(np.linspace(700, 740, 60))
        spy_df = pd.DataFrame({
            'symbol': ['SPY'] * 60,
            'close': spy_prices,
            'timestamp': pd.date_range('2026-03-01', periods=60, freq='D')
        })

        mock_bars = MagicMock()
        mock_bars.df = spy_df.set_index(['symbol', 'timestamp'])
        rm.client.get_stock_bars = MagicMock(return_value=mock_bars)

        rm.current_regime = Regime.BULL

        mock_yf_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=MagicMock(
            iloc=MagicMock(__getitem__=MagicMock(return_value=21.0))
        ))
        mock_yf_ticker.return_value.history.return_value = mock_hist

        with patch.dict('sys.modules', {'yfinance': MagicMock(Ticker=mock_yf_ticker)}):
            result = rm.assess()
            self.assertNotEqual(result, Regime.BULL,
                "VIX=21 debe sacar de BULL (exit threshold es 20)")


class TestRegimeManagerMarketHours(unittest.TestCase):
    """Fix 3: No evaluar fuera de horario de mercado."""

    def setUp(self):
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()
            self.rm.current_regime = Regime.CHOP

    @patch('engine.regime_manager.RegimeManager._is_market_hours', return_value=False)
    def test_assess_if_needed_skips_overnight(self, mock_hours):
        """assess_if_needed() no debe llamar assess() fuera de horario."""
        with patch.object(self.rm, 'assess') as mock_assess:
            result = self.rm.assess_if_needed()
            mock_assess.assert_not_called()
            self.assertEqual(result, Regime.CHOP)

    @patch('engine.regime_manager.RegimeManager._is_market_hours', return_value=True)
    def test_assess_if_needed_runs_during_market(self, mock_hours):
        """assess_if_needed() SÍ debe llamar assess() durante horario de mercado."""
        from engine.regime_manager import _LAST_HOURLY_ASSESS
        _LAST_HOURLY_ASSESS["ts"] = None  # Forzar que necesita re-evaluar

        with patch.object(self.rm, 'assess', return_value=Regime.BULL) as mock_assess:
            result = self.rm.assess_if_needed()
            mock_assess.assert_called_once()

    def test_is_market_hours_weekday(self):
        """_is_market_hours debe retornar True en horario de mercado."""
        # Lunes a las 10:00 NY
        with patch('engine.regime_manager.datetime') as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 0  # Lunes
            mock_now.hour = 10
            mock_dt.now.return_value = mock_now
            self.assertTrue(RegimeManager._is_market_hours())

    def test_is_market_hours_weekend(self):
        """_is_market_hours debe retornar False en fin de semana."""
        with patch('engine.regime_manager.datetime') as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 5  # Sábado
            mock_dt.now.return_value = mock_now
            self.assertFalse(RegimeManager._is_market_hours())

    def test_is_market_hours_overnight(self):
        """_is_market_hours debe retornar False de noche."""
        with patch('engine.regime_manager.datetime') as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 1  # Martes
            mock_now.hour = 3  # 3 AM
            mock_dt.now.return_value = mock_now
            self.assertFalse(RegimeManager._is_market_hours())


class TestRegimeManagerDailyBars(unittest.TestCase):
    """Fix 1: Verificar que usamos barras diarias, no intradía."""

    def setUp(self):
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()

    def test_sma_periods_are_daily(self):
        """SMA_PERIOD debe ser 50 (días) y SMA_FAST debe ser 20 (días)."""
        self.assertEqual(self.rm.SMA_PERIOD, 50)
        self.assertEqual(self.rm.SMA_FAST_PERIOD, 20)

    def test_assess_uses_daily_timeframe(self):
        """assess() debe solicitar TimeFrame.Day, no barras de 5min."""
        import pandas as pd
        import numpy as np

        spy_prices = list(np.linspace(700, 740, 60))
        spy_df = pd.DataFrame({
            'symbol': ['SPY'] * 60,
            'close': spy_prices,
            'timestamp': pd.date_range('2026-03-01', periods=60, freq='D')
        })
        mock_bars = MagicMock()
        mock_bars.df = spy_df.set_index(['symbol', 'timestamp'])

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

        # Verificar que la llamada usó TimeFrame.Day
        call_args = self.rm.client.get_stock_bars.call_args_list[0]
        request = call_args[0][0]
        # TimeFrame.Day crea nuevos objetos, comparar por atributos internos
        self.assertEqual(request.timeframe.amount, 1, "TimeFrame amount debe ser 1")
        self.assertEqual(str(request.timeframe.unit.value), "Day",
            "assess() debe usar TimeFrame.Day, no barras intradía")


class TestRegimeManagerFallback(unittest.TestCase):
    """Fix 4: VIXY fallback mejorado."""

    def setUp(self):
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            self.rm = RegimeManager()
            self.rm.current_regime = Regime.BULL

    def test_fallback_keeps_previous_regime_when_no_vix(self):
        """Si VIX no está disponible y ya hay un régimen previo, mantenerlo."""
        import pandas as pd
        import numpy as np

        spy_prices = list(np.linspace(700, 740, 60))
        spy_df = pd.DataFrame({
            'symbol': ['SPY'] * 60,
            'close': spy_prices,
            'timestamp': pd.date_range('2026-03-01', periods=60, freq='D')
        })
        mock_bars = MagicMock()
        mock_bars.df = spy_df.set_index(['symbol', 'timestamp'])

        # VIXY también falla
        vixy_df = pd.DataFrame({
            'symbol': ['VIXY'],
            'close': [20.0],
            'timestamp': pd.date_range('2026-05-01', periods=1, freq='D')
        })
        mock_vixy_bars = MagicMock()
        mock_vixy_bars.df = vixy_df.set_index(['symbol', 'timestamp'])

        call_count = [0]
        def mock_get_bars(req):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_bars  # SPY
            return mock_vixy_bars  # VIXY (solo 1 barra, insuficiente)

        self.rm.client.get_stock_bars = mock_get_bars

        # yfinance falla
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = Exception("yf down")
        with patch.dict('sys.modules', {'yfinance': mock_yf}):
            result = self.rm.assess()
            # Debe mantener BULL (régimen anterior) en lugar de cambiar a UNKNOWN
            self.assertEqual(result, Regime.BULL,
                "Sin VIX, debe mantener régimen anterior, no forzar UNKNOWN")


class TestRegimeManagerStrategyMaps(unittest.TestCase):
    """Verificar consistencia de los mapas de estrategia por régimen."""

    def test_all_regimes_have_etf_strategies(self):
        """Todos los regímenes deben tener al menos una estrategia ETF."""
        for regime in Regime:
            strategies = REGIME_ETF_MAP.get(regime, [])
            self.assertGreater(len(strategies), 0,
                f"Régimen {regime.value} no tiene estrategias ETF")

    def test_all_regimes_have_crypto_strategies(self):
        """Todos los regímenes deben tener al menos una estrategia Crypto."""
        for regime in Regime:
            strategies = REGIME_CRYPTO_MAP.get(regime, [])
            self.assertGreater(len(strategies), 0,
                f"Régimen {regime.value} no tiene estrategias Crypto")

    def test_sizing_exists_for_all_regimes(self):
        """Todos los regímenes deben tener un sizing definido."""
        for regime in Regime:
            self.assertIn(regime.value, SIZING_BY_REGIME,
                f"Falta sizing para régimen {regime.value}")

    def test_is_strategy_enabled_respects_engine(self):
        """is_strategy_enabled debe respetar el parámetro engine."""
        from engine.regime_manager import _CURRENT_REGIME
        with patch('engine.regime_manager.StockHistoricalDataClient'):
            rm = RegimeManager()

        # Simular régimen BULL
        _CURRENT_REGIME["regime"] = "BULL"

        # Strat 1 está en ETF BULL pero no en Equities BULL
        self.assertTrue(rm.is_strategy_enabled(1, engine="etf"))
        self.assertFalse(rm.is_strategy_enabled(1, engine="equities"))

        # Strat 2 está en Equities BULL
        self.assertTrue(rm.is_strategy_enabled(2, engine="equities"))


class TestRegimeManagerLogOutput(unittest.TestCase):
    """Fix 5: Log debe mostrar estrategias reales, no 'Todas activas!'."""

    def test_assess_log_does_not_say_todas_activas(self):
        """El log NO debe contener 'Todas activas!' hardcodeado."""
        import inspect
        from engine.regime_manager import RegimeManager
        source = inspect.getsource(RegimeManager.assess)
        self.assertNotIn("Todas activas!", source,
            "El log aún contiene 'Todas activas!' hardcodeado")

    def test_assess_log_contains_strategy_lists(self):
        """El log debe mostrar estrategias por motor (ETF:, EQ:, Crypto:)."""
        import inspect
        from engine.regime_manager import RegimeManager
        source = inspect.getsource(RegimeManager.assess)
        self.assertTrue(
            "ETF:" in source or "ETF:[" in source,
            "El log debe mostrar estrategias ETF"
        )
        self.assertTrue(
            "EQ:" in source or "EQ:[" in source,
            "El log debe mostrar estrategias Equities"
        )
        self.assertTrue(
            "Crypto:" in source or "Crypto:[" in source,
            "El log debe mostrar estrategias Crypto"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
