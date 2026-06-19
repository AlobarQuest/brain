"""Tests for the MCPPrefixAlias ASGI shim."""
import pytest

from src.core.mcp_alias import MCPPrefixAlias


class CapturingApp:
    """Stub ASGI app that records the scope it receives."""

    def __init__(self):
        self.received_path: str | None = None
        self.received_raw_path: bytes | None = None
        self.received_root_path: str | None = None
        self.received_app_root_path: str | None = None
        self.called: bool = False

    async def __call__(self, scope, receive, send):
        self.called = True
        self.received_path = scope["path"]
        self.received_raw_path = scope.get("raw_path")
        self.received_root_path = scope.get("root_path")
        self.received_app_root_path = scope.get("app_root_path")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def make_scope(path: str, raw_path: bytes | None = None, root_path: str = "") -> dict:
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": path,
        "root_path": root_path,
        "query_string": b"",
        "headers": [],
    }
    if raw_path is not None:
        scope["raw_path"] = raw_path
    return scope


async def noop_receive():
    return {}


async def noop_send(message):
    pass


@pytest.mark.asyncio
async def test_mcp_path_gets_trailing_slash():
    """/mcp is rewritten to /mcp/ before the inner app sees the scope."""
    inner = CapturingApp()
    alias = MCPPrefixAlias(inner, "/mcp")

    await alias(make_scope("/mcp"), noop_receive, noop_send)

    assert inner.received_path == "/mcp/"


@pytest.mark.asyncio
async def test_mcp_path_already_slash_is_idempotent():
    """/mcp/ passed in stays /mcp/ — rstrip('/') + '/' is idempotent."""
    inner = CapturingApp()
    alias = MCPPrefixAlias(inner, "/mcp")

    await alias(make_scope("/mcp/"), noop_receive, noop_send)

    assert inner.received_path == "/mcp/"


@pytest.mark.asyncio
async def test_non_mcp_path_is_forwarded_to_inner_app():
    """/api/health is forwarded to the inner app unchanged in spirit.

    In production the alias is registered only for the /mcp route so /api/health
    never reaches it; here we verify the shim doesn't error or skip calling the
    inner app when the path happens not to be /mcp.
    """
    inner = CapturingApp()
    alias = MCPPrefixAlias(inner, "/mcp")

    await alias(make_scope("/api/health"), noop_receive, noop_send)

    assert inner.called
    # The shim always appends a trailing slash (it is designed for the /mcp route);
    # the path value is /api/health/ — no /mcp prefix is injected.
    assert inner.received_path == "/api/health/"
    assert not inner.received_path.startswith("/mcp")


@pytest.mark.asyncio
async def test_raw_path_gets_trailing_slash_when_present():
    """raw_path also has b'/' appended when the scope contains it."""
    inner = CapturingApp()
    alias = MCPPrefixAlias(inner, "/mcp")

    await alias(make_scope("/mcp", raw_path=b"/mcp"), noop_receive, noop_send)

    assert inner.received_raw_path == b"/mcp/"


@pytest.mark.asyncio
async def test_raw_path_omitted_when_not_in_scope():
    """If raw_path is absent from the scope, it stays absent — no KeyError."""
    inner = CapturingApp()
    alias = MCPPrefixAlias(inner, "/mcp")

    await alias(make_scope("/mcp"), noop_receive, noop_send)  # no raw_path kwarg

    assert inner.received_raw_path is None


@pytest.mark.asyncio
async def test_root_path_gets_mount_path_prepended():
    """root_path gains the mount_path prefix so the mounted app computes paths correctly."""
    inner = CapturingApp()
    alias = MCPPrefixAlias(inner, "/mcp")

    await alias(make_scope("/mcp", root_path=""), noop_receive, noop_send)

    assert inner.received_root_path == "/mcp"


@pytest.mark.asyncio
async def test_app_root_path_preserves_original_root_path():
    """app_root_path is set to the original root_path before the mount prefix is added."""
    inner = CapturingApp()
    alias = MCPPrefixAlias(inner, "/mcp")

    await alias(make_scope("/mcp", root_path="/prefix"), noop_receive, noop_send)

    assert inner.received_app_root_path == "/prefix"
    assert inner.received_root_path == "/prefix/mcp"


@pytest.mark.asyncio
async def test_mount_path_trailing_slash_stripped():
    """MCPPrefixAlias('/mcp/') and MCPPrefixAlias('/mcp') behave identically."""
    inner_a = CapturingApp()
    inner_b = CapturingApp()

    alias_a = MCPPrefixAlias(inner_a, "/mcp/")
    alias_b = MCPPrefixAlias(inner_b, "/mcp")

    scope = make_scope("/mcp")
    await alias_a(scope, noop_receive, noop_send)
    await alias_b(scope, noop_receive, noop_send)

    assert inner_a.received_root_path == inner_b.received_root_path
