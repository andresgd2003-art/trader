"""
tests/conftest.py
==================
Mock automático de Alpaca para poder ejecutar los tests de sanidad
sin necesitar la librería alpaca-py instalada localmente.

En el VPS/Docker alpaca está instalado, pero en CI y máquinas de desarrollo
no siempre está disponible. Este conftest.py se ejecuta automáticamente
antes de cualquier test gracias a pytest.
"""
import sys
import types


def _mock(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# Solo mockear si alpaca no está instalado
try:
    import alpaca  # type: ignore
except ModuleNotFoundError:
    # ── Estructura de paquetes ─────────────────────────────────────────────
    alpaca        = _mock('alpaca')
    al_trading    = _mock('alpaca.trading')
    al_tr_client  = _mock('alpaca.trading.client')
    al_tr_req     = _mock('alpaca.trading.requests')
    al_tr_enums   = _mock('alpaca.trading.enums')
    al_tr_models  = _mock('alpaca.trading.models')
    al_data       = _mock('alpaca.data')
    al_data_hist  = _mock('alpaca.data.historical')
    al_data_live  = _mock('alpaca.data.live')
    al_data_news  = _mock('alpaca.data.live.news')
    al_data_mod   = _mock('alpaca.data.models')
    al_data_req   = _mock('alpaca.data.requests')
    al_data_tf    = _mock('alpaca.data.timeframe')
    al_common     = _mock('alpaca.common')
    al_common_exc = _mock('alpaca.common.exceptions')

    # ── Clases mínimas ─────────────────────────────────────────────────────
    # alpaca.trading.client
    class _TradingClient:
        def __init__(self, *a, **kw): pass
    al_tr_client.TradingClient = _TradingClient

    # alpaca.trading.requests
    class _Req:
        def __init__(self, *a, **kw): pass
    al_tr_req.MarketOrderRequest     = _Req
    al_tr_req.LimitOrderRequest      = _Req
    al_tr_req.TakeProfitRequest      = _Req
    al_tr_req.StopLossRequest        = _Req
    al_tr_req.BracketOrderRequest    = _Req
    al_tr_req.GetOrdersRequest       = _Req
    al_tr_req.GetPortfolioHistoryRequest = _Req

    # alpaca.trading.enums
    class _Side:
        BUY = 'buy'; SELL = 'sell'
        def __init__(self, v=None): self.value = v
    class _TIF:
        DAY = 'day'; GTC = 'gtc'
        def __init__(self, v=None): self.value = v
    class _QOS:
        ALL = 'all'
        def __init__(self, v=None): self.value = v
    class _OType:
        MARKET = 'market'; LIMIT = 'limit'
        def __init__(self, v=None): self.value = v
    al_tr_enums.OrderSide       = _Side
    al_tr_enums.TimeInForce     = _TIF
    al_tr_enums.QueryOrderStatus = _QOS
    al_tr_enums.OrderType       = _OType

    # alpaca.data.live.news
    class _NewsStream:
        def __init__(self, *a, **kw): pass
    al_data_news.NewsDataStream = _NewsStream

    # alpaca.data.models
    class _News:
        headlines = []
    al_data_mod.News = _News

    # alpaca.data.timeframe
    class _TimeFrame:
        Day = 'day'; Minute = 'minute'; Hour = 'hour'
    al_data_tf.TimeFrame = _TimeFrame

    # alpaca.data.historical
    class _StockClient:
        def __init__(self, *a, **kw): pass
    class _CryptoClient:
        def __init__(self, *a, **kw): pass
    al_data_hist.StockHistoricalDataClient  = _StockClient
    al_data_hist.CryptoHistoricalDataClient = _CryptoClient

    # alpaca.data.live.crypto
    al_data_live_crypto = _mock('alpaca.data.live.crypto')
    class _CryptoStream:
        def __init__(self, *a, **kw): pass
        def subscribe_bars(self, *a, **kw): pass
        def run(self): pass
    al_data_live_crypto.CryptoDataStream = _CryptoStream

    # alpaca.data.requests
    class _StockBarsReq:
        def __init__(self, *a, **kw): pass
    al_data_req.StockBarsRequest = _StockBarsReq

    # alpaca.common.exceptions
    class _APIError(Exception): pass
    al_common_exc.APIError = _APIError
