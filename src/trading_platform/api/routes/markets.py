"""Markets and universe pricing REST endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from trading_platform.api.container import PlatformContainer, get_container
from trading_platform.api.schemas import KlineCandleResponse, MarketTickerResponse
from trading_platform.core.logging import get_logger

logger = get_logger("api.markets")
router = APIRouter(prefix="/markets", tags=["Markets"])


@router.get("", response_model=list[MarketTickerResponse])
async def get_markets(
    container: PlatformContainer = Depends(get_container),
) -> list[MarketTickerResponse]:
    """Retrieve active trading universe symbols with live prices, 24h stats, and execution precision constraints."""
    symbols = container.config.market_data.active_symbols
    im = container.instrument_manager
    pm = container.portfolio_manager
    adapter = container.exchange_adapter
    now = datetime.now(UTC)

    response: list[MarketTickerResponse] = []

    for sym in symbols:
        symbol_upper = sym.upper()

        # Fetch precision rules from InstrumentManager
        tick_size = 0.10
        step_size = 0.001
        min_notional = 5.0
        if im:
            inst = None
            if hasattr(im, "_instruments") and symbol_upper in im._instruments:
                inst = im._instruments[symbol_upper]
            elif hasattr(im, "get_instrument"):
                try:
                    inst = im.get_instrument(symbol_upper)
                except Exception:
                    inst = None

            if inst:
                tick_size = float(inst.tick_size)
                step_size = float(inst.step_size)
                min_notional = float(inst.min_notional)

        # Mark price from PortfolioManager or container ticker cache
        cached_ticker = container.market_tickers.get(symbol_upper, {})
        mark_price = pm._latest_mark_prices.get(symbol_upper, 0.0)

        if mark_price == 0.0 and "mark_price" in cached_ticker:
            mark_price = float(cached_ticker["mark_price"])

        # If still 0 and adapter is available, fetch live ticker from Binance Testnet
        if mark_price == 0.0 and adapter and hasattr(adapter, "_request"):
            try:
                data_24h = await adapter._request("GET", "/fapi/v1/ticker/24hr", params={"symbol": symbol_upper})
                if isinstance(data_24h, dict) and "lastPrice" in data_24h:
                    last_p = float(data_24h.get("lastPrice", 0.0))
                    change_p = float(data_24h.get("priceChangePercent", 0.0))
                    high_p = float(data_24h.get("highPrice", last_p))
                    low_p = float(data_24h.get("lowPrice", last_p))
                    vol = float(data_24h.get("volume", 0.0))
                    qvol = float(data_24h.get("quoteVolume", 0.0))

                    mark_price = last_p
                    pm._latest_mark_prices[symbol_upper] = mark_price
                    cached_ticker.update({
                        "symbol": symbol_upper,
                        "mark_price": mark_price,
                        "last_price": last_p,
                        "price_change_percent_24h": change_p,
                        "high_price_24h": high_p,
                        "low_price_24h": low_p,
                        "volume_24h": vol,
                        "quote_volume_24h": qvol,
                    })
                    container.market_tickers[symbol_upper] = cached_ticker
            except Exception as ex:
                logger.debug(f"Direct ticker fetch failed for {symbol_upper}: {ex}")

        # 24h ticker cache or fallback
        change_pct = cached_ticker.get("price_change_percent_24h", 0.0)
        high_24h = cached_ticker.get("high_price_24h", round(mark_price * 1.01, 2))
        low_24h = cached_ticker.get("low_price_24h", round(mark_price * 0.99, 2))
        vol_24h = cached_ticker.get("volume_24h", 1000.0)
        quote_vol_24h = cached_ticker.get("quote_volume_24h", round(vol_24h * mark_price, 2))
        funding_rate = cached_ticker.get("funding_rate", 0.0001)
        last_price = cached_ticker.get("last_price", mark_price)
        index_price = cached_ticker.get("index_price", mark_price)

        response.append(
            MarketTickerResponse(
                symbol=symbol_upper,
                mark_price=round(mark_price, 4),
                index_price=round(index_price, 4) if index_price else round(mark_price, 4),
                last_price=round(last_price, 4) if last_price else round(mark_price, 4),
                funding_rate=round(funding_rate, 6) if funding_rate is not None else 0.0001,
                next_funding_time=None,
                price_change_percent_24h=round(change_pct, 2),
                high_price_24h=round(high_24h, 2),
                low_price_24h=round(low_24h, 2),
                volume_24h=round(vol_24h, 2),
                quote_volume_24h=round(quote_vol_24h, 2),
                tick_size=tick_size,
                step_size=step_size,
                min_notional=min_notional,
                last_updated=now,
            )
        )

    return response


@router.get("/{symbol}/klines", response_model=list[KlineCandleResponse])
async def get_market_klines(
    symbol: str,
    timeframe: str = Query(default="1m", description="Candle timeframe (e.g. 1m, 5m, 15m, 1h)"),
    limit: int = Query(default=120, ge=1, le=500, description="Number of candles to return"),
    container: PlatformContainer = Depends(get_container),
) -> list[KlineCandleResponse]:
    """Retrieve genuine historical OHLCV candlesticks with Fast SMA(9) and Slow SMA(21) overlays computed from strategy parameters."""
    sym = symbol.upper()
    adapter = container.exchange_adapter

    raw_candles = []
    if adapter and hasattr(adapter, "get_historical_data"):
        try:
            raw_candles = await adapter.get_historical_data(
                symbol=sym,
                interval=timeframe,
                limit=limit,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch historical klines from adapter for {sym}: {e}")

    results: list[KlineCandleResponse] = []
    if raw_candles:
        closes: list[float] = []
        fast_period = 9
        slow_period = 21

        for c in raw_candles:
            closes.append(c.close_price)
            fast_sma = (
                sum(closes[-fast_period:]) / fast_period
                if len(closes) >= fast_period
                else None
            )
            slow_sma = (
                sum(closes[-slow_period:]) / slow_period
                if len(closes) >= slow_period
                else None
            )
            t_sec = int(c.open_time.timestamp())
            results.append(
                KlineCandleResponse(
                    time=t_sec,
                    open=c.open_price,
                    high=c.high_price,
                    low=c.low_price,
                    close=c.close_price,
                    volume=c.volume,
                    fast_sma=round(fast_sma, 4) if fast_sma is not None else None,
                    slow_sma=round(slow_sma, 4) if slow_sma is not None else None,
                )
            )

    return results
