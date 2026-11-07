"""Environment registry tools, against a real in-memory SQLite engine."""

from datetime import date

import pytest
from fastmcp import FastMCP
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import src.brains.infra.tools.environments as env_tools_module
from src.brains.infra.models import Environment
from src.brains.infra.repositories.environments import EnvironmentRepository
from src.core.address_guard import payload_from_environments
from src.core.config import BrainType
from src.core.registry import load_brain

ADDRS = {
    "ipv4": "40.160.102.182",
    "tailnet_node": "ovh-vps1",
    "tailnet_ip": "100.92.138.19",
    "hostnames": ["coolify-1.devonwatkins.com"],
}


@pytest.fixture
async def mcp(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Environment.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(env_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(env_tools_module, "require_approver", lambda: True)
    server = FastMCP("t")
    load_brain(BrainType.INFRA).register(server)
    yield server
    await engine.dispose()


async def _call(mcp: FastMCP, tool: str, **args) -> dict:
    result = await mcp.call_tool(tool, args)
    assert result.structured_content is not None
    return result.structured_content


async def _retired() -> list[str]:
    async with env_tools_module.get_session_factory()() as session:
        rows = await EnvironmentRepository(session).list_all()
    (role,) = payload_from_environments(rows)["roles"]
    return sorted(role["retired"])


def _set_args(**overrides) -> dict:
    args = {
        "role": "prod-vps",
        "description": "Production VPS",
        "provider": "ovh",
        "location": "Vint Hill, VA",
        "addresses": dict(ADDRS),
        "current_since": "2026-10-23",
    }
    args.update(overrides)
    return args


async def test_set_environment_requires_approver(mcp, monkeypatch):
    monkeypatch.setattr(env_tools_module, "require_approver", lambda: False)
    body = await _call(mcp, "set_environment", **_set_args())
    assert body["error"] == "not_authorized"


async def test_set_environment_creates_row(mcp):
    prev = [
        {"addresses": {"ipv4": "178.156.247.239"}, "provider": "hetzner", "until": "2026-10-23"}
    ]
    created = await _call(mcp, "set_environment", **_set_args(previous=prev))
    assert created["rotated"] is False
    got = await _call(mcp, "get_environment", role="prod-vps")
    assert got["addresses"] == ADDRS
    assert got["previous"] == prev
    assert got["current_since"] == "2026-10-23"


async def test_set_environment_rotates_on_address_change(mcp):
    await _call(mcp, "set_environment", **_set_args())
    moved = dict(ADDRS, ipv4="203.0.113.9")
    body = await _call(mcp, "set_environment", **_set_args(addresses=moved, provider="other"))
    assert body["rotated"] is True
    assert body["previous"] == [
        {"addresses": ADDRS, "provider": "ovh", "until": date.today().isoformat()}
    ]
    assert body["addresses"]["ipv4"] == "203.0.113.9"
    assert body["provider"] == "other"
    # Only the address that changed is retired; the unchanged node, tailnet IP and hostname
    # are still live.
    assert await _retired() == ["40.160.102.182"]


async def test_rotation_through_tailnet_ip_only_change(mcp):
    await _call(mcp, "set_environment", **_set_args())
    moved = dict(ADDRS, tailnet_ip="100.64.0.7")
    body = await _call(mcp, "set_environment", **_set_args(addresses=moved))
    assert body["rotated"] is True
    assert await _retired() == ["100.92.138.19"]


async def test_rotation_through_ipv6_only_change(mcp):
    await _call(mcp, "set_environment", **_set_args(addresses=dict(ADDRS, ipv6="2001:db8::1")))
    moved = dict(ADDRS, ipv6="2001:db8::2")
    body = await _call(mcp, "set_environment", **_set_args(addresses=moved))
    assert body["rotated"] is True
    assert await _retired() == ["2001:db8::1"]


async def test_set_environment_same_addresses_no_rotation(mcp):
    await _call(mcp, "set_environment", **_set_args())
    again = _set_args(description="Edited", addresses=dict(ADDRS, hostnames=["x.example.com"]))
    body = await _call(mcp, "set_environment", **again)
    assert body["rotated"] is False
    assert body["previous"] == []
    assert body["description"] == "Edited"


async def test_set_environment_explicit_previous_replaces(mcp):
    await _call(mcp, "set_environment", **_set_args())
    prev = [{"addresses": {"ipv4": "1.2.3.4"}, "provider": "hetzner", "until": "2026-01-01"}]
    moved = dict(ADDRS, ipv4="203.0.113.9")
    body = await _call(mcp, "set_environment", **_set_args(addresses=moved, previous=prev))
    assert body["rotated"] is False
    assert body["previous"] == prev


async def test_set_environment_rejects_unknown_address_keys(mcp):
    body = await _call(mcp, "set_environment", **_set_args(addresses={"dns": "x"}))
    assert body["error"] == "invalid_addresses"
    assert "ipv4" in body["allowed"] and "hostnames" in body["allowed"]
    bad_prev = [{"addresses": {"dns": "x"}, "provider": "p", "until": "2026-01-01"}]
    body = await _call(mcp, "set_environment", **_set_args(previous=bad_prev))
    assert body["error"] == "invalid_addresses"


async def test_set_environment_accepts_empty_addresses(mcp):
    body = await _call(mcp, "set_environment", **_set_args(addresses={}))
    assert body["addresses"] == {}


async def test_set_environment_rejects_bad_dates(mcp):
    body = await _call(mcp, "set_environment", **_set_args(current_since="soon"))
    assert body["error"] == "invalid_date"
    prev = [{"addresses": {}, "provider": "p", "until": "later"}]
    body = await _call(mcp, "set_environment", **_set_args(previous=prev))
    assert body["error"] == "invalid_date"


async def test_get_environment_not_found(mcp):
    body = await _call(mcp, "get_environment", role="nope")
    assert body == {"error": "not_found", "role": "nope"}


async def test_list_environments_any_key(mcp, monkeypatch):
    await _call(mcp, "set_environment", **_set_args())
    monkeypatch.setattr(env_tools_module, "require_approver", lambda: False)
    body = await _call(mcp, "list_environments")
    assert [e["role"] for e in body["environments"]] == ["prod-vps"]
