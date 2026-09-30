import pytest
import respx


@pytest.fixture(autouse=True)
def no_network():
    """Any unmocked HTTP request fails, so tests never hit Open-Meteo for real."""
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        yield mock
