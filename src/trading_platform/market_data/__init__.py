"""Market data ingestion, normalization, gap recovery, and persistence."""

from trading_platform.market_data.candle_persister import CandlePersister
from trading_platform.market_data.gap_handler import MarketDataGapHandler
from trading_platform.market_data.normalizer import BinanceDataNormalizer
from trading_platform.market_data.ws_client import BinanceWebSocketClient

__all__ = [
    "BinanceDataNormalizer",
    "MarketDataGapHandler",
    "BinanceWebSocketClient",
    "CandlePersister",
]
