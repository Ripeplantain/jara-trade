"""Chooses the data source once, at startup."""
from __future__ import annotations

import logging

from .cache import PriceCache
from .config import MarketSettings
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(
    price_cache: PriceCache, settings: MarketSettings | None = None
) -> MarketDataSource:
    """Massive if MASSIVE_API_KEY is set and non-blank, otherwise the simulator."""
    settings = settings or MarketSettings.from_env()

    if settings.use_massive:
        # Deferred import: a simulator-only run never loads httpx, and vice versa numpy.
        from .massive_client import MassiveClient, MassiveDataSource

        logger.info("Market data source: Massive REST API")
        return MassiveDataSource(
            price_cache,
            MassiveClient(settings.massive_api_key),
            poll_interval=settings.massive_poll_interval,
            eod_poll_interval=settings.massive_eod_poll_interval,
        )

    from .simulator import SimulatorDataSource

    logger.info("Market data source: simulator")
    return SimulatorDataSource(
        price_cache,
        update_interval=settings.simulator_interval,
        seed=settings.simulator_seed,
    )
