import pytest

from rotkehlchen.externalapis.coingecko import Coingecko
from rotkehlchen.externalapis.cryptocompare import Cryptocompare
from rotkehlchen.history.events import EventsHistorian
from rotkehlchen.history.price import PriceHistorian
from rotkehlchen.history.types import DEFAULT_HISTORICAL_PRICE_ORACLES_ORDER
from rotkehlchen.tests.utils.history import maybe_mock_historical_price_queries


@pytest.fixture(name='cryptocompare')
def fixture_cryptocompare(data_dir, database):
    return Cryptocompare(data_directory=data_dir, database=database)


@pytest.fixture(scope='session', name='session_cryptocompare')
def fixture_session_cryptocompare(session_data_dir, session_database):
    return Cryptocompare(data_directory=session_data_dir, database=session_database)


@pytest.fixture(scope='session', name='session_coingecko')
def fixture_session_coingecko():
    return Coingecko()


@pytest.fixture(name='historical_price_oracles_order')
def fixture_historical_price_oracles_order():
    return DEFAULT_HISTORICAL_PRICE_ORACLES_ORDER


@pytest.fixture(name='dont_mock_price_for')
def fixture_dont_mock_price_for():
    return []


@pytest.fixture
def price_historian(
        data_dir,
        inquirer,  # pylint: disable=unused-argument
        should_mock_price_queries,
        mocked_price_queries,
        cryptocompare,
        session_coingecko,
        default_mock_price_value,
        historical_price_oracles_order,
        dont_mock_price_for,
):
    # Since this is a singleton and we want it initialized everytime the fixture
    # is called make sure its instance is always starting from scratch
    PriceHistorian._PriceHistorian__instance = None
    historian = PriceHistorian(
        data_directory=data_dir,
        cryptocompare=cryptocompare,
        coingecko=session_coingecko,
    )
    historian.set_oracles_order(historical_price_oracles_order)
    maybe_mock_historical_price_queries(
        historian=historian,
        should_mock_price_queries=should_mock_price_queries,
        mocked_price_queries=mocked_price_queries,
        default_mock_value=default_mock_price_value,
        dont_mock_price_for=dont_mock_price_for,
    )

    return historian


@pytest.fixture
def events_historian(
        database,
        data_dir,
        function_scope_messages_aggregator,
        blockchain,
        evm_transaction_decoder,
        exchange_manager,
):
    historian = EventsHistorian(
        user_directory=data_dir,
        db=database,
        msg_aggregator=function_scope_messages_aggregator,
        exchange_manager=exchange_manager,
        chain_manager=blockchain,
        evm_tx_decoder=evm_transaction_decoder,
    )
    return historian
