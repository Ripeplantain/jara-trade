from app.market.cache import PriceCache
from app.market.config import MarketSettings
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


async def test_factory_picks_simulator_without_key():
    source = create_market_data_source(PriceCache(), MarketSettings())
    assert isinstance(source, SimulatorDataSource)


async def test_factory_picks_massive_with_key():
    source = create_market_data_source(PriceCache(), MarketSettings(massive_api_key="k"))
    assert isinstance(source, MassiveDataSource)
    await source.stop()  # closes the httpx client
