import logging

import httpx
import pytest
import respx

from src.core import address_guard
from src.core.address_guard import AddressBook, fetch_address_book

URL = "https://infra-brain.test/api/environments/addresses"
PAYLOAD = {"roles": [{"role": "prod-vps", "current": ["40.160.102.182"], "retired": ["1.2.3.4"]}]}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(address_guard, "_clock", c)
    return c


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("BRAIN_TYPE", "app")
    monkeypatch.setenv("MCP_ACCESS_KEY", "a" * 64)
    monkeypatch.setenv("POSTGRES_HOST", "h")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "d")
    monkeypatch.setenv("INFRA_BRAIN_URL", "https://infra-brain.test")


@respx.mock
async def test_fetch_returns_book(clock):
    respx.get(URL).respond(json=PAYLOAD)
    book = await fetch_address_book()
    assert book == AddressBook(
        current={"40.160.102.182": "prod-vps"}, retired={"1.2.3.4": "prod-vps"}
    )


@respx.mock
async def test_fetch_caches_success(clock):
    route = respx.get(URL).respond(json=PAYLOAD)
    first = await fetch_address_book()
    clock.now += address_guard.CACHE_TTL_S - 1
    second = await fetch_address_book()
    assert first is second
    assert route.call_count == 1
    clock.now += 2
    await fetch_address_book()
    assert route.call_count == 2


@respx.mock
async def test_fetch_sends_identifying_user_agent(clock):
    route = respx.get(URL).respond(json=PAYLOAD)
    await fetch_address_book()
    assert route.calls[0].request.headers["user-agent"] == "brain-address-guard/1 (app-brain)"


@respx.mock
async def test_fetch_403_logs_and_returns_none(clock, caplog):
    respx.get(URL).respond(403)
    with caplog.at_level(logging.WARNING, logger=address_guard.logger.name):
        assert await fetch_address_book() is None
    assert "address guard skipped" in caplog.text


@respx.mock
async def test_fetch_timeout_returns_none(clock, caplog):
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with caplog.at_level(logging.WARNING, logger=address_guard.logger.name):
        assert await fetch_address_book() is None
    assert "address guard skipped" in caplog.text


@respx.mock
async def test_fetch_bad_json_returns_none(clock, caplog):
    respx.get(URL).respond(200, content=b"<html>not json")
    with caplog.at_level(logging.WARNING, logger=address_guard.logger.name):
        assert await fetch_address_book() is None
    assert "address guard skipped" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"roles": "x"},
        {"roles": [1]},
        {"roles": [{"current": ["a"]}]},
        {"roles": [{"role": "r", "current": 5}]},
        {"roles": [{"role": "r", "retired": "1.2.3.4"}]},
        {"roles": [{"role": "r", "current": "1.2.3.4"}]},
        {"roles": [{"role": "r", "current": [5]}]},
        {"roles": [{"role": "r", "retired": [None]}]},
        {"roles": [{"role": 7, "current": ["a"]}]},
    ],
)
@respx.mock
async def test_fetch_malformed_payload_returns_none(clock, caplog, body):
    respx.get(URL).respond(json=body)
    with caplog.at_level(logging.WARNING, logger=address_guard.logger.name):
        assert await fetch_address_book() is None
    assert "address guard skipped" in caplog.text


@respx.mock
async def test_failure_cached_briefly(clock):
    route = respx.get(URL).respond(500)
    assert await fetch_address_book() is None
    clock.now += address_guard.FAILURE_TTL_S - 1
    assert await fetch_address_book() is None
    assert route.call_count == 1
    clock.now += 2
    assert await fetch_address_book() is None
    assert route.call_count == 2


@respx.mock
async def test_reset_address_cache_forces_refetch(clock):
    route = respx.get(URL).respond(json=PAYLOAD)
    await fetch_address_book()
    address_guard.reset_address_cache()
    await fetch_address_book()
    assert route.call_count == 2


async def test_malformed_infra_brain_url_returns_none(clock, caplog, monkeypatch):
    monkeypatch.setenv("INFRA_BRAIN_URL", "http://host:notaport")
    with caplog.at_level(logging.WARNING, logger=address_guard.logger.name):
        assert await fetch_address_book() is None
    assert "address guard skipped" in caplog.text
