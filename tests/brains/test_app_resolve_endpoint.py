"""Tests for the app-brain REST resolve endpoint (GET /api/apps/resolve):
  - pure matching helpers (normalize_host, match_environment)
  - the route's behavior (200 shape, 404, 400, auth) with the repo mocked

No DB required (matches the repo's mock-based style). The end-to-end path against
real Postgres + the JSONB rows is proven separately in the PR's verification step.
"""
import httpx
import pytest

from src.brains.app.repositories.apps import normalize_host, match_environment

KEY = "a" * 64

# Booking's live records (the validated reference).
BOOKING_ROWS = [
    {
        "github_repo": "AlobarQuest/booking-system",
        "environments": [
            {"name": "prod", "branch": "master", "url": "https://booking.devonwatkins.com", "coolify_app_uuid": "hkw488ggssgcskk0ooc0ksk0"},
            {"name": "preview", "branch": "preview", "url": "https://preview.booking.devonwatkins.com", "coolify_app_uuid": "yscogs0wggcgco8g4wwk0o0g"},
        ],
    },
    {"github_repo": "AlobarQuest/watchtower", "environments": []},
    {"github_repo": None, "environments": [{"name": "prod", "branch": None, "url": None, "coolify_app_uuid": None}]},
]


class TestNormalizeHost:
    @pytest.mark.parametrize("raw,expected", [
        ("https://booking.devonwatkins.com", "booking.devonwatkins.com"),
        ("https://Booking.devonwatkins.com/", "booking.devonwatkins.com"),
        ("booking.devonwatkins.com", "booking.devonwatkins.com"),
        ("http://x.example.com/path?q=1", "x.example.com"),
        ("BOOKING.devonwatkins.com.", "booking.devonwatkins.com"),
        (None, None),
        ("", None),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_host(raw) == expected


class TestMatchEnvironment:
    def test_uuid_exact_prod(self):
        assert match_environment(BOOKING_ROWS, coolify_app_uuid="hkw488ggssgcskk0ooc0ksk0") == {
            "github_repo": "AlobarQuest/booking-system", "name": "prod",
            "branch": "master", "url": "https://booking.devonwatkins.com",
        }

    def test_uuid_exact_preview(self):
        rec = match_environment(BOOKING_ROWS, coolify_app_uuid="yscogs0wggcgco8g4wwk0o0g")
        assert rec["name"] == "preview" and rec["branch"] == "preview"

    def test_fqdn_fallback_normalizes(self):
        for fqdn in ("booking.devonwatkins.com", "https://booking.devonwatkins.com/", "BOOKING.devonwatkins.com"):
            rec = match_environment(BOOKING_ROWS, fqdn=fqdn)
            assert rec["name"] == "prod" and rec["branch"] == "master", fqdn

    def test_uuid_preferred_over_fqdn(self):
        # uuid points at preview, fqdn at prod → uuid wins (tried first)
        rec = match_environment(BOOKING_ROWS, coolify_app_uuid="yscogs0wggcgco8g4wwk0o0g", fqdn="booking.devonwatkins.com")
        assert rec["name"] == "preview"

    def test_uuid_miss_falls_back_to_fqdn(self):
        rec = match_environment(BOOKING_ROWS, coolify_app_uuid="nope", fqdn="preview.booking.devonwatkins.com")
        assert rec["name"] == "preview"

    def test_unknown_returns_none(self):
        assert match_environment(BOOKING_ROWS, coolify_app_uuid="nope") is None
        assert match_environment(BOOKING_ROWS, fqdn="unknown.example.com") is None

    def test_null_branch_returned_as_is_not_guessed(self):
        rows = [{"github_repo": "x/y", "environments": [{"name": "prod", "branch": None, "url": "https://z.example.com", "coolify_app_uuid": "u1"}]}]
        assert match_environment(rows, coolify_app_uuid="u1") == {
            "github_repo": "x/y", "name": "prod", "branch": None, "url": "https://z.example.com",
        }

    def test_empty_environments_and_no_params(self):
        assert match_environment([{"github_repo": "x/y", "environments": []}], coolify_app_uuid="u") is None
        assert match_environment(BOOKING_ROWS) is None  # neither param


# ---------------------------------------------------------------------------
# Route behavior — real app brain wired via create_app, repo mocked
# ---------------------------------------------------------------------------

@pytest.fixture
def env_setup(monkeypatch):
    from src.core.config import get_settings
    monkeypatch.setenv("BRAIN_TYPE", "app")
    monkeypatch.setenv("MCP_ACCESS_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "d")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_repo(monkeypatch, result):
    """Make the route's repo return `result` without touching a DB."""
    import src.core.db as db_module
    import src.brains.app.repositories.apps as apps_repo

    monkeypatch.setattr(db_module, "get_session_factory", lambda: (lambda: _FakeSession()))

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def resolve_environment(self, coolify_app_uuid=None, fqdn=None):
            return result

    monkeypatch.setattr(apps_repo, "AppRepository", _FakeRepo)


async def _client(env_setup):
    from src.core.app import create_app
    app = create_app()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_resolve_200_returns_record(env_setup, monkeypatch):
    rec = {"github_repo": "AlobarQuest/booking-system", "name": "prod", "branch": "master", "url": "https://booking.devonwatkins.com"}
    _patch_repo(monkeypatch, rec)
    async with await _client(env_setup) as client:
        resp = await client.get("/api/apps/resolve", params={"coolify_app_uuid": "hkw488ggssgcskk0ooc0ksk0"}, headers={"x-brain-key": KEY})
    assert resp.status_code == 200
    assert resp.json() == rec


async def test_resolve_404_on_no_match(env_setup, monkeypatch):
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.get("/api/apps/resolve", params={"coolify_app_uuid": "nope"}, headers={"x-brain-key": KEY})
    assert resp.status_code == 404


async def test_resolve_400_when_no_params(env_setup, monkeypatch):
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.get("/api/apps/resolve", headers={"x-brain-key": KEY})
    assert resp.status_code == 400


async def test_resolve_401_without_key(env_setup, monkeypatch):
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.get("/api/apps/resolve", params={"fqdn": "booking.devonwatkins.com"})
    assert resp.status_code == 401


async def test_resolve_key_via_query_param_accepted(env_setup, monkeypatch):
    rec = {"github_repo": "x/y", "name": "prod", "branch": "master", "url": "https://z"}
    _patch_repo(monkeypatch, rec)
    async with await _client(env_setup) as client:
        resp = await client.get("/api/apps/resolve", params={"fqdn": "z", "key": KEY})
    assert resp.status_code == 200
