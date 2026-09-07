"""Instrument Manager for universe discovery, caching, and precision validation."""

import time
from decimal import Decimal

from trading_platform.core.config import AppConfig
from trading_platform.core.exceptions import (
    ConfigurationError,
    InstrumentNotFoundError,
    OrderValidationError,
)
from trading_platform.core.logging import get_logger
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.models.instrument import Instrument

logger = get_logger("exchange.instrument_manager")


class InstrumentManager:
    """Manages trading universe metadata, caching, precision formatting, and pre-submission validation."""

    def __init__(
        self,
        config: AppConfig,
        adapter: ExchangeAdapter,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.cache_ttl_seconds = cache_ttl_seconds

        # Explicit configured trading universe
        self.configured_symbols: list[str] = [s.upper() for s in config.market_data.active_symbols]

        # Internal cache
        self._instruments: dict[str, Instrument] = {}
        self._last_refresh_time: float = 0.0
        self._verified_leverages: dict[str, int] = {}
        self._verified_margin_modes: dict[str, str] = {}

    @property
    def is_cache_stale(self) -> bool:
        """Check if market metadata cache requires refresh."""
        return (
            time.time() - self._last_refresh_time
        ) > self.cache_ttl_seconds or not self._instruments

    async def initialize(self) -> None:
        """Initial fetch of market specifications and explicit margin/leverage setup with fail-fast verification."""
        await self.refresh_markets(force=True)
        await self.configure_margin_and_leverage()

    async def configure_margin_and_leverage(self) -> None:
        """Explicitly set margin mode and leverage on exchange for every symbol in active universe, then verify."""
        target_margin_mode = getattr(self.config.risk, "default_margin_mode", "ISOLATED")
        if hasattr(target_margin_mode, "value"):
            target_margin_mode = target_margin_mode.value
        target_margin_mode = str(target_margin_mode).upper()

        default_lev = getattr(self.config.risk, "default_symbol_leverage", 5)
        custom_levs = getattr(self.config.risk, "symbol_leverages", {})

        for symbol in self.configured_symbols:
            target_lev = custom_levs.get(symbol, default_lev)
            logger.info(
                f"Configuring exchange margin mode={target_margin_mode} and leverage={target_lev}x for {symbol}..."
            )
            # 1. Set Margin Mode
            try:
                await self.adapter.set_margin_type(symbol, target_margin_mode)
            except Exception as e:
                logger.critical(
                    f"FAIL-FAST: Failed to set margin mode {target_margin_mode} on exchange for {symbol}: {e}"
                )
                raise ConfigurationError(
                    f"Exchange margin configuration failed for {symbol}: {e}. "
                    f"Ensure any open positions are closed before changing margin mode."
                ) from e

            # 2. Set Leverage
            try:
                await self.adapter.set_leverage(symbol, target_lev)
            except Exception as e:
                logger.critical(
                    f"FAIL-FAST: Failed to set leverage {target_lev}x on exchange for {symbol}: {e}"
                )
                raise ConfigurationError(
                    f"Exchange leverage configuration failed for {symbol}: {e}"
                ) from e

            # 3. Fail-fast Verification Step: Query exchange ground-truth
            try:
                state = await self.adapter.get_symbol_leverage_and_margin(symbol)
                if isinstance(state, dict):
                    actual_margin = str(state.get("margin_type", "")).upper()
                    actual_lev = int(state.get("leverage", 0))

                    if actual_margin != target_margin_mode:
                        raise ConfigurationError(
                            f"Fail-fast verification failed for {symbol}: Exchange margin mode mismatch. "
                            f"Expected {target_margin_mode}, got {actual_margin}."
                        )
                    if actual_lev != target_lev:
                        raise ConfigurationError(
                            f"Fail-fast verification failed for {symbol}: Exchange leverage mismatch. "
                            f"Expected {target_lev}x, got {actual_lev}x."
                        )

                    self._verified_margin_modes[symbol] = actual_margin
                    self._verified_leverages[symbol] = actual_lev
                    logger.info(
                        f"VERIFIED: Symbol {symbol} confirmed on exchange as {actual_margin} margin at {actual_lev}x leverage."
                    )
                else:
                    self._verified_margin_modes[symbol] = target_margin_mode
                    self._verified_leverages[symbol] = target_lev
            except ConfigurationError:
                raise
            except Exception as e:
                logger.critical(
                    f"FAIL-FAST: Verification query failed for {symbol} on exchange: {e}"
                )
                raise ConfigurationError(
                    f"Could not verify margin/leverage ground truth for {symbol}: {e}"
                ) from e

    def get_verified_leverage(self, symbol: str) -> int:
        """Return real confirmed exchange leverage multiplier for symbol."""
        sym = symbol.upper()
        if sym in self._verified_leverages:
            return self._verified_leverages[sym]
        return getattr(self.config.risk, "default_symbol_leverage", 5)

    def get_verified_margin_mode(self, symbol: str) -> str:
        """Return real confirmed exchange margin mode for symbol."""
        sym = symbol.upper()
        if sym in self._verified_margin_modes:
            return self._verified_margin_modes[sym]
        target_mode = getattr(self.config.risk, "default_margin_mode", "ISOLATED")
        return target_mode.value if hasattr(target_mode, "value") else str(target_mode).upper()

    async def refresh_markets(self, force: bool = False) -> list[Instrument]:
        """Fetch market specifications via the ExchangeAdapter and filter to explicit universe.

        Args:
            force: If True, bypasses cache TTL and refetches immediately.
        """
        if not force and not self.is_cache_stale:
            return list(self._instruments.values())

        logger.info(
            f"Refreshing market specifications via adapter for universe: {self.configured_symbols}"
        )
        all_markets = await self.adapter.get_markets()

        configured_set = set(self.configured_symbols)
        new_instruments: dict[str, Instrument] = {}

        for inst in all_markets:
            is_active = getattr(inst, "is_active", True)
            if inst.symbol in configured_set and (is_active is not False):
                new_instruments[inst.symbol] = inst

        # Check for missing symbols
        missing = configured_set - set(new_instruments.keys())
        if missing:
            logger.warning(f"Configured symbols not found or not active on exchange: {missing}")

        self._instruments = new_instruments
        self._last_refresh_time = time.time()
        logger.info(f"Instrument Manager loaded {len(self._instruments)} active trading pairs.")
        return list(self._instruments.values())

    def get_instrument(self, symbol: str) -> Instrument:
        """Retrieve instrument specifications for a symbol."""
        sym = symbol.upper()
        if sym not in self._instruments:
            if sym in self.configured_symbols:
                from trading_platform.core.constants import ContractType
                return Instrument(
                    symbol=sym,
                    base_asset=sym.replace("USDT", ""),
                    quote_asset="USDT",
                    contract_type=ContractType.PERPETUAL,
                    tick_size=Decimal("0.10"),
                    step_size=Decimal("0.001"),
                    min_qty=Decimal("0.001"),
                    min_notional=Decimal("5.0"),
                    maker_fee=Decimal("0.0002"),
                    taker_fee=Decimal("0.0005"),
                    is_active=True,
                )
            raise InstrumentNotFoundError(
                f"Symbol '{sym}' not found in active trading universe. Available: {list(self._instruments.keys())}"
            )
        return self._instruments[sym]

    def list_universe(self) -> list[str]:
        """Return list of active symbols in configured trading universe."""
        return list(self._instruments.keys())

    def get_active_instruments(self) -> list[Instrument]:
        """Return all active Instrument objects."""
        return list(self._instruments.values())

    def round_price(self, symbol: str, price: float | Decimal) -> Decimal:
        """Round price down to instrument tick size precision."""
        inst = self.get_instrument(symbol)
        dec_price = Decimal(str(price))
        tick = inst.tick_size
        return (dec_price // tick) * tick

    def round_quantity(self, symbol: str, quantity: float | Decimal) -> Decimal:
        """Round order quantity down to instrument step size precision."""
        inst = self.get_instrument(symbol)
        dec_qty = Decimal(str(quantity))
        step = inst.step_size
        # Quantize down to step size
        rounded = (dec_qty // step) * step
        return rounded

    def validate_order_precision(
        self,
        symbol: str,
        quantity: float | Decimal,
        price: float | Decimal | None = None,
    ) -> None:
        """Validate order quantity, price tick size, and minimum notional client-side before submission.

        Raises:
            OrderValidationError: If order fails any instrument precision rule.
        """
        inst = self.get_instrument(symbol)
        dec_qty = Decimal(str(quantity))

        # 1. Minimum quantity check
        if dec_qty < inst.min_qty:
            raise OrderValidationError(
                f"Order quantity {dec_qty} for {symbol} is below minimum allowed {inst.min_qty}"
            )

        # 2. Step size remainder check
        step_rem = dec_qty % inst.step_size
        if step_rem != Decimal("0"):
            # If remainder is not 0 within Decimal precision
            if abs(step_rem) > Decimal("1e-12") and abs(step_rem - inst.step_size) > Decimal(
                "1e-12"
            ):
                raise OrderValidationError(
                    f"Order quantity {dec_qty} for {symbol} is not a multiple of step size {inst.step_size}"
                )

        # 3. Price tick size check (if price provided)
        if price is not None:
            dec_price = Decimal(str(price))
            tick_rem = dec_price % inst.tick_size
            if tick_rem != Decimal("0"):
                if abs(tick_rem) > Decimal("1e-12") and abs(tick_rem - inst.tick_size) > Decimal(
                    "1e-12"
                ):
                    raise OrderValidationError(
                        f"Order price {dec_price} for {symbol} is not a multiple of tick size {inst.tick_size}"
                    )

            # 4. Minimum notional check
            notional = dec_qty * dec_price
            if notional < inst.min_notional:
                raise OrderValidationError(
                    f"Order notional value ${notional:.2f} for {symbol} is below minimum notional ${inst.min_notional}"
                )
