"""Normalizer converting raw Binance WebSocket payloads into domain events."""

import json
from datetime import UTC, datetime
from typing import Any

from trading_platform.core.events import CandleEvent, Event, MarketDataEvent
from trading_platform.core.logging import get_logger

logger = get_logger("market_data.normalizer")


class BinanceDataNormalizer:
    """Transforms raw Binance WebSocket JSON payloads into platform-native typed events."""

    def normalize(self, raw_payload: str | dict[str, Any]) -> list[Event]:
        """Parse raw stream payload and return list of normalized domain events.

        Supports both combined stream wrappers ({"stream": "...", "data": {...}})
        and direct event payloads ({"e": "kline", ...}).
        """
        try:
            data = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except Exception as e:
            logger.error(f"Failed to parse JSON WebSocket payload: {e}")
            return []

        # Unwrap combined stream container if present
        if "data" in data and isinstance(data["data"], dict):
            event_data = data["data"]
            stream_name = data.get("stream", "")
        else:
            event_data = data
            stream_name = ""

        event_type = event_data.get("e")

        # 1. Kline / Candlestick Event
        if event_type == "kline" or "@kline" in stream_name:
            kline_event = self._normalize_kline(event_data)
            return [kline_event] if kline_event else []

        # 2. Mark Price & Funding Rate Event
        if event_type == "markPriceUpdate" or "@markPrice" in stream_name:
            mark_price_event = self._normalize_mark_price(event_data)
            return [mark_price_event] if mark_price_event else []

        # 3. 24hr Rolling Window Ticker Event
        if event_type == "24hrTicker" or "@ticker" in stream_name:
            ticker_event = self._normalize_24hr_ticker(event_data)
            return [ticker_event] if ticker_event else []

        return []

    def _normalize_24hr_ticker(self, data: dict[str, Any]) -> MarketDataEvent | None:
        """Parse Binance 24hrTicker payload into MarketDataEvent."""
        try:
            symbol = data.get("s", "").upper()
            last_price = float(data.get("c", 0.0))
            change_pct = float(data.get("P", 0.0))
            high_24h = float(data.get("h", last_price))
            low_24h = float(data.get("l", last_price))
            vol_24h = float(data.get("v", 0.0))
            quote_vol_24h = float(data.get("q", 0.0))

            return MarketDataEvent(
                symbol=symbol,
                mark_price=last_price,
                last_price=last_price,
                price_change_percent_24h=change_pct,
                high_price_24h=high_24h,
                low_price_24h=low_24h,
                volume_24h=vol_24h,
                quote_volume_24h=quote_vol_24h,
            )
        except Exception as e:
            logger.error(f"Error normalizing Binance 24hrTicker payload: {e} | data={data}")
            return None

    def _normalize_kline(self, data: dict[str, Any]) -> CandleEvent | None:
        """Parse Binance kline payload into CandleEvent."""
        k = data.get("k")
        if not k:
            return None

        try:
            symbol = k.get("s", data.get("s", "")).upper()
            timeframe = k.get("i", "1m")
            open_ms = k.get("t", 0)
            close_ms = k.get("T", 0)
            is_closed = bool(k.get("x", False))

            open_dt = datetime.fromtimestamp(open_ms / 1000.0, tz=UTC)
            close_dt = datetime.fromtimestamp(close_ms / 1000.0, tz=UTC)

            return CandleEvent(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_dt,
                close_time=close_dt,
                open_price=float(k.get("o", 0.0)),
                high_price=float(k.get("h", 0.0)),
                low_price=float(k.get("l", 0.0)),
                close_price=float(k.get("c", 0.0)),
                volume=float(k.get("v", 0.0)),
                quote_volume=float(k.get("q", 0.0)),
                trades_count=int(k.get("n", 0)),
                is_closed=is_closed,
            )
        except Exception as e:
            logger.error(f"Error normalizing Binance kline payload: {e} | data={k}")
            return None

    def _normalize_mark_price(self, data: dict[str, Any]) -> MarketDataEvent | None:
        """Parse Binance markPriceUpdate payload into MarketDataEvent."""
        try:
            symbol = data.get("s", "").upper()
            mark_price = float(data.get("p", 0.0))
            index_price = float(data.get("i", 0.0)) if "i" in data else None

            funding_rate_val = data.get("r")
            funding_rate = float(funding_rate_val) if funding_rate_val is not None else None

            next_funding_ms = data.get("T")
            next_funding_time = (
                datetime.fromtimestamp(next_funding_ms / 1000.0, tz=UTC)
                if next_funding_ms
                else None
            )

            return MarketDataEvent(
                symbol=symbol,
                mark_price=mark_price,
                index_price=index_price,
                funding_rate=funding_rate,
                next_funding_time=next_funding_time,
            )
        except Exception as e:
            logger.error(f"Error normalizing Binance markPrice payload: {e} | data={data}")
            return None
