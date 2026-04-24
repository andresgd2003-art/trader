import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

# Strategy Imports
from strategies.strat_11_inverse_momentum import InverseMomentumETF
from strategies_equities.strat_04_defensive_rotation import DefensiveRotation
from strategies_crypto.strat_12_mean_reversion_extreme import CryptoMeanReversionExtreme

class MockOrderManager:
    def __init__(self, paper=True):
        self.paper = paper
        self.buy = AsyncMock()
        self.buy_dynamic = AsyncMock()
        self.buy_bracket = AsyncMock()
        self.sell = AsyncMock()
        self.sell_exact = AsyncMock()
        self.close_position = AsyncMock()
        self.sync_position_from_alpaca = MagicMock(return_value=(False, 0))
        self.request_buy = AsyncMock(return_value=True)
        self.release_asset = AsyncMock()
        self._calculate_crypto_qty = MagicMock(return_value=0.001)
        self.client = MagicMock()
        mock_account = MagicMock()
        mock_account.settled_cash = "1000.0"
        self.client.get_account.return_value = mock_account

    def _client_id(self, strategy):
        return f"test_eq_{strategy}"

class MockRegimeManager:
    def __init__(self, enabled=True, regime="BEAR"):
        self.enabled = enabled
        self._regime = regime
        
    def is_strategy_enabled(self, strat_num, engine="etf"):
        return self.enabled
        
    def get_current_regime(self):
        return {"regime": self._regime}

class MockBar:
    def __init__(self, symbol, close):
        self.symbol = symbol
        self.close = close

# --- TESTS FOR INVERSE MOMENTUM ETF ---
@pytest.fixture
def etf_strat():
    om = MockOrderManager()
    rm = MockRegimeManager(enabled=True, regime="BEAR")
    return InverseMomentumETF(order_manager=om, regime_manager=rm)

@pytest.mark.asyncio
async def test_etf_smoke():
    # Smoke import is covered by just importing it at the top
    assert InverseMomentumETF is not None

@pytest.mark.asyncio
async def test_etf_instantiation(etf_strat):
    assert etf_strat.STRAT_NUMBER == 11
    assert "QQQ" in etf_strat.symbols
    assert "SPY" in etf_strat.symbols

@pytest.mark.asyncio
async def test_etf_signal_trigger(etf_strat):
    # Needs MACD hist < 0 and close < SMA200
    # Feed 200 bars going down to trigger it
    for i in range(200):
        # descending prices to create MACD < 0 and price < SMA200
        await etf_strat.on_bar(MockBar("QQQ", 200.0 - i * 0.1))
    
    etf_strat.order_manager.buy.assert_called_once()
    args, kwargs = etf_strat.order_manager.buy.call_args
    assert args[0] == "SQQQ"
    assert kwargs.get("strategy_name") == "InverseMomentumETF"

@pytest.mark.asyncio
async def test_etf_regime_gate():
    om = MockOrderManager()
    rm = MockRegimeManager(enabled=False, regime="BULL")
    strat = InverseMomentumETF(order_manager=om, regime_manager=rm)
    
    for i in range(200):
        await strat.on_bar(MockBar("QQQ", 200.0 - i * 0.1))
    
    strat.order_manager.buy.assert_not_called()

@pytest.mark.asyncio
async def test_etf_exit_condition(etf_strat):
    # Enter first
    for i in range(200):
        await etf_strat.on_bar(MockBar("QQQ", 200.0 - i * 0.1))
    
    etf_strat.order_manager.buy.assert_called()
    etf_strat.order_manager.buy.reset_mock()
    
    # Now feed bars going UP to make MACD hist > 0
    for i in range(30):
        await etf_strat.on_bar(MockBar("QQQ", 180.0 + i * 2.0))
        
    # Should have triggered sell
    assert etf_strat.order_manager.sell.called or etf_strat.order_manager.sell_exact.called


# --- TESTS FOR DEFENSIVE ROTATION ---
def _set_global_regime(r):
    import engine.regime_manager as rmod
    rmod._CURRENT_REGIME["regime"] = r

@pytest.fixture
def eq_strat():
    om = MockOrderManager()
    rm = MockRegimeManager(enabled=True, regime="BEAR")
    _set_global_regime("BEAR")
    return DefensiveRotation(order_manager=om, regime_manager=rm)

@pytest.mark.asyncio
async def test_eq_smoke():
    assert DefensiveRotation is not None

@pytest.mark.asyncio
async def test_eq_instantiation(eq_strat):
    assert eq_strat.STRAT_NUMBER == 4
    assert "KO" in eq_strat.symbols
    assert "SPY" in eq_strat.symbols

@pytest.mark.asyncio
async def test_eq_signal_trigger(eq_strat):
    # Need SPY RSI < 40 and regime BEAR
    for i in range(20):
        await eq_strat.on_bar(MockBar("SPY", 400.0 - i * 2.0))
    for ticker in eq_strat.symbols:
        if ticker == "SPY": continue
        for i in range(20):
            await eq_strat.on_bar(MockBar(ticker, 50.0 - i * 0.1))
    eq_strat.order_manager.buy_bracket.assert_called()

@pytest.mark.asyncio
async def test_eq_regime_gate():
    om = MockOrderManager()
    rm = MockRegimeManager(enabled=False, regime="BULL")
    _set_global_regime("BULL")
    strat = DefensiveRotation(order_manager=om, regime_manager=rm)
    
    # If regime is BULL and enabled is False, it shouldn't buy
    for i in range(20):
        await strat.on_bar(MockBar("SPY", 400.0 - i * 2.0))
    for ticker in strat.symbols:
        if ticker == "SPY": continue
        for i in range(20):
            await strat.on_bar(MockBar(ticker, 50.0 - i * 0.1))
    strat.order_manager.buy_bracket.assert_not_called()

@pytest.mark.asyncio
async def test_eq_exit_condition(eq_strat):
    for i in range(20):
        await eq_strat.on_bar(MockBar("SPY", 400.0 - i * 2.0))
    for ticker in eq_strat.symbols:
        if ticker == "SPY": continue
        for i in range(20):
            await eq_strat.on_bar(MockBar(ticker, 50.0 - i * 0.1))
    eq_strat.order_manager.buy_bracket.assert_called()

    # Force exit: SPY RSI > 55
    for i in range(20):
        await eq_strat.on_bar(MockBar("SPY", 370.0 + i * 5.0))
    for ticker in eq_strat.symbols:
        if ticker == "SPY": continue
        await eq_strat.on_bar(MockBar(ticker, 52.0))

    assert (eq_strat.order_manager.sell.called
            or eq_strat.order_manager.sell_exact.called
            or eq_strat.order_manager.close_position.called)


# --- TESTS FOR CRYPTO MEAN REVERSION EXTREME ---
@pytest.fixture
def crypto_strat():
    om = MockOrderManager()
    rm = MockRegimeManager(enabled=True, regime="BEAR")
    return CryptoMeanReversionExtreme(order_manager=om, regime_manager=rm)

@pytest.mark.asyncio
async def test_crypto_smoke():
    assert CryptoMeanReversionExtreme is not None

@pytest.mark.asyncio
async def test_crypto_instantiation(crypto_strat):
    assert crypto_strat.STRAT_NUMBER == 12
    assert "BTC/USD" in crypto_strat.symbols

@pytest.mark.asyncio
async def test_crypto_signal_trigger(crypto_strat):
    # RSI < 25 and close < BB lower
    for i in range(20):
        await crypto_strat.on_bar(MockBar("BTC/USD", 60000.0 + (i%2)*100))
    for i in range(5):
        await crypto_strat.on_bar(MockBar("BTC/USD", 60000.0 - i*5000.0))
        
    crypto_strat.order_manager.buy.assert_called()
    _, kwargs = crypto_strat.order_manager.buy.call_args
    assert kwargs.get("symbol") == "BTC/USD"
    assert "Mean Reversion" in kwargs.get("strategy_name", "")

@pytest.mark.asyncio
async def test_crypto_regime_gate():
    om = MockOrderManager()
    rm = MockRegimeManager(enabled=False, regime="BULL")
    strat = CryptoMeanReversionExtreme(order_manager=om, regime_manager=rm)
    
    for i in range(25):
        await strat.on_bar(MockBar("BTC/USD", 60000.0 - i * 1000.0))
        
    strat.order_manager.buy.assert_not_called()

@pytest.mark.asyncio
async def test_crypto_exit_condition(crypto_strat):
    # Enter first
    for i in range(20):
        await crypto_strat.on_bar(MockBar("BTC/USD", 60000.0 + (i%2)*100))
    for i in range(5):
        await crypto_strat.on_bar(MockBar("BTC/USD", 60000.0 - i*5000.0))
        
    crypto_strat.order_manager.buy.assert_called()
    
    # Exit condition: RSI > 50 or close >= BB middle
    for i in range(15):
        await crypto_strat.on_bar(MockBar("BTC/USD", 35000.0 + i * 2000.0))
        
    assert crypto_strat.order_manager.sell.called or crypto_strat.order_manager.sell_exact.called
