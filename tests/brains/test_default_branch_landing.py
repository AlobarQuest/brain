"""Tests for the default-branch landing fact (WS-P2.29):
  - the pure fold (aggregate_landing) over the apps one repository feeds
  - GET /api/apps/default-branch-landing behavior, with the repo mocked
  - the read-only key: it reaches the read paths and nothing else
  - the determination file's own shape, and its vocabulary against the model
    and against the migration's frozen copy

No DB required (matches the repo's mock-based style).
"""

import json
import pathlib

import httpx
import pytest

from src.brains.app.models import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    LANDING_UNKNOWN,
    LANDING_VALUES,
    landing_in_clause,
)
from src.brains.app.repositories.apps import aggregate_landing

KEY = "a" * 64
READ_KEY = "b" * 64
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

BRAIN = "AlobarQuest/brain"


def _row(slug, landing, evidence="read the workflow"):
    return {
        "slug": slug,
        "default_branch_landing": landing,
        "determined_at": "2026-08-02T00:00:00+00:00" if landing else None,
        "evidence": evidence if landing else None,
    }


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------


class TestAggregateLanding:
    def test_no_matching_app_is_unknown_not_inert(self):
        """App Brain not knowing a repository is never evidence merging is safe."""
        out = aggregate_landing("AlobarQuest/nope", [])
        assert out["landing"] == LANDING_UNKNOWN
        assert out["reason"] == "no_app_record"
        assert out["apps"] == []

    def test_single_redeploying_app(self):
        out = aggregate_landing("AlobarQuest/change-manager", [_row("change-manager", "redeploys")])
        assert out["landing"] == LANDING_REDEPLOYS
        assert out["reason"] is None

    def test_single_inert_app(self):
        out = aggregate_landing("AlobarQuest/orchestrator", [_row("orchestrator", "inert")])
        assert out["landing"] == LANDING_INERT
        assert out["reason"] is None

    def test_unassessed_app_is_unknown_distinguishable_from_inert(self):
        out = aggregate_landing("AlobarQuest/x", [_row("x", None)])
        assert out["landing"] == LANDING_UNKNOWN
        assert out["reason"] == "not_assessed"
        # and it is NOT the same answer as an assessed non-deploying app
        assert aggregate_landing("AlobarQuest/y", [_row("y", "inert")])["landing"] == LANDING_INERT

    def test_four_brains_one_repository_all_redeploy(self):
        """AlobarQuest/brain feeds four running services from one ci.yml."""
        rows = [
            _row("app-brain", "redeploys"),
            _row("code-brain", "redeploys"),
            _row("infra-brain", "redeploys"),
            _row("open-brain", "redeploys"),
        ]
        out = aggregate_landing(BRAIN, rows)
        assert out["landing"] == LANDING_REDEPLOYS
        assert [a["slug"] for a in out["apps"]] == [
            "app-brain",
            "code-brain",
            "infra-brain",
            "open-brain",
        ]

    def test_one_redeploying_sibling_settles_the_answer(self):
        """Asking a single slug would answer for a quarter of what the merge moves."""
        rows = [_row("open-brain", "inert"), _row("app-brain", "redeploys")]
        assert aggregate_landing(BRAIN, rows)["landing"] == LANDING_REDEPLOYS

    def test_redeploys_wins_over_an_unassessed_sibling(self):
        """Already knowing something redeploys is not weakened by an unknown."""
        rows = [_row("app-brain", "redeploys"), _row("code-brain", None)]
        out = aggregate_landing(BRAIN, rows)
        assert out["landing"] == LANDING_REDEPLOYS

    def test_inert_requires_every_sibling_assessed(self):
        """One unassessed sibling makes the whole repository unknown, not inert."""
        rows = [_row("open-brain", "inert"), _row("code-brain", None)]
        out = aggregate_landing(BRAIN, rows)
        assert out["landing"] == LANDING_UNKNOWN
        assert out["reason"] == "not_assessed"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_landing_in_clause_has_no_trailing_comma():
    """A one-element tuple's repr renders `IN ('x',)`, a Postgres syntax error."""
    assert landing_in_clause() == "default_branch_landing IN ('redeploys', 'inert')"
    assert ",)" not in landing_in_clause()


def test_migration_frozen_vocabulary_matches_the_model():
    """0005 inlines a frozen copy (migrations must not import the model)."""
    src = (
        REPO_ROOT / "src/brains/app/migrations/versions/0005_default_branch_landing.py"
    ).read_text()
    ns: dict = {}
    for line in src.splitlines():
        if line.startswith("_LANDING_VALUES"):
            exec(line, ns)
    assert ns["_LANDING_VALUES"] == LANDING_VALUES


def test_read_paths_name_routes_that_actually_exist(env_setup):
    """READ_PATHS is matched EXACTLY by the middleware, and the route paths are
    literals in their decorators — so a rename would silently leave the read key
    unable to reach the route it exists for, with no test failing. Pin them.
    """
    from src.brains.app import READ_PATHS
    from src.core.app import create_app

    routes = {
        r.path: getattr(r, "methods", set()) for r in create_app().routes if hasattr(r, "path")
    }
    for path in READ_PATHS:
        assert path in routes, f"{path} is in READ_PATHS but is not a route"
        assert "GET" in routes[path], f"{path} is a read path but has no GET"


def test_unknown_is_never_a_stored_value():
    assert LANDING_UNKNOWN not in LANDING_VALUES


# ---------------------------------------------------------------------------
# Route behavior — real app brain wired via create_app, repo mocked
# ---------------------------------------------------------------------------


@pytest.fixture
def env_setup(monkeypatch):
    from src.core.config import get_settings

    monkeypatch.setenv("BRAIN_TYPE", "app")
    monkeypatch.setenv("MCP_ACCESS_KEY", KEY)
    monkeypatch.setenv("READ_KEY", READ_KEY)
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
    import src.brains.app.repositories.apps as apps_repo
    import src.core.db as db_module

    monkeypatch.setattr(db_module, "get_session_factory", lambda: lambda: _FakeSession())

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def resolve_default_branch_landing(self, github_repo):
            return result

    monkeypatch.setattr(apps_repo, "AppRepository", _FakeRepo)


async def _client(env_setup):
    from src.core.app import create_app

    app = create_app()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_landing_200_returns_answer(env_setup, monkeypatch):
    rec = aggregate_landing("AlobarQuest/change-manager", [_row("change-manager", "redeploys")])
    _patch_repo(monkeypatch, rec)
    async with await _client(env_setup) as client:
        resp = await client.get(
            "/api/apps/default-branch-landing",
            params={"github_repo": "AlobarQuest/change-manager"},
            headers={"x-brain-key": KEY},
        )
    assert resp.status_code == 200
    assert resp.json()["landing"] == "redeploys"


async def test_landing_unknown_is_200_not_404(env_setup, monkeypatch):
    """'unknown' is an answer and must appear on the wire as one — a 404 would be
    indistinguishable from the route being absent because the brain is stale."""
    _patch_repo(monkeypatch, aggregate_landing("AlobarQuest/nope", []))
    async with await _client(env_setup) as client:
        resp = await client.get(
            "/api/apps/default-branch-landing",
            params={"github_repo": "AlobarQuest/nope"},
            headers={"x-brain-key": KEY},
        )
    assert resp.status_code == 200
    assert resp.json()["landing"] == "unknown"
    assert resp.json()["reason"] == "no_app_record"


async def test_landing_400_without_repo(env_setup, monkeypatch):
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.get("/api/apps/default-branch-landing", headers={"x-brain-key": KEY})
    assert resp.status_code == 400


async def test_landing_401_without_key(env_setup, monkeypatch):
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.get("/api/apps/default-branch-landing", params={"github_repo": "x/y"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# The read-only key
# ---------------------------------------------------------------------------


async def test_read_key_reaches_the_landing_route(env_setup, monkeypatch):
    rec = aggregate_landing("AlobarQuest/orchestrator", [_row("orchestrator", "inert")])
    _patch_repo(monkeypatch, rec)
    async with await _client(env_setup) as client:
        resp = await client.get(
            "/api/apps/default-branch-landing",
            params={"github_repo": "AlobarQuest/orchestrator"},
            headers={"x-brain-key": READ_KEY},
        )
    assert resp.status_code == 200
    assert resp.json()["landing"] == "inert"


async def test_read_key_is_refused_on_the_mcp_surface(env_setup, monkeypatch):
    """The whole point: a read key cannot reach any write tool."""
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.post(
            "/mcp/",
            headers={"x-brain-key": READ_KEY, "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
    assert resp.status_code == 401


async def test_read_key_is_refused_on_an_undeclared_path(env_setup, monkeypatch):
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.get("/openapi.json", headers={"x-brain-key": READ_KEY})
    assert resp.status_code == 401


async def test_read_key_is_refused_on_a_non_get_method(env_setup, monkeypatch):
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.post(
            "/api/apps/default-branch-landing",
            params={"github_repo": "x/y"},
            headers={"x-brain-key": READ_KEY},
        )
    assert resp.status_code == 401


async def test_access_key_still_reaches_everything(env_setup, monkeypatch):
    """The read key is additive: it must not narrow the approver key."""
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.get("/openapi.json", headers={"x-brain-key": KEY})
    assert resp.status_code == 200


def test_read_key_must_differ_from_the_other_keys(monkeypatch):
    from src.core.config import Settings

    base = dict(
        brain_type="app",
        postgres_host="h",
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
    )
    with pytest.raises(ValueError, match="read_key must differ"):
        Settings(mcp_access_key=KEY, read_key=KEY, **base)
    with pytest.raises(ValueError, match="read_key must differ"):
        Settings(mcp_access_key=KEY, contributor_key=READ_KEY, read_key=READ_KEY, **base)


@pytest.mark.parametrize("via", ["header", "query"])
async def test_non_ascii_key_is_401_not_500(env_setup, monkeypatch, via):
    """hmac.compare_digest raises TypeError on a str above U+007F, and Starlette
    decodes headers as latin-1 — so before this was compared on bytes, an
    unauthenticated caller could 500 the auth layer with one header.

    The header is sent as raw bytes because httpx refuses to encode a non-ASCII
    header from a str; those bytes are exactly what a hand-rolled client puts on
    the wire, and Starlette hands them to the middleware as 'kéy...'.
    """
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        kwargs = (
            {"headers": {"x-brain-key": "kéy-with-non-ascii".encode("latin-1")}}
            if via == "header"
            else {"params": {"key": "kéy-with-non-ascii"}}
        )
        resp = await client.get(
            "/api/apps/default-branch-landing",
            params={"github_repo": "x/y", **kwargs.pop("params", {})},
            **kwargs,
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# The determination file (backfill input)
# ---------------------------------------------------------------------------


class TestDeterminationFile:
    @pytest.fixture
    def apps(self):
        from scripts.backfill_default_branch_landing import load_determination

        return load_determination()

    def test_loads_and_validates(self, apps):
        assert len(apps) == 25

    def test_every_row_carries_evidence_naming_what_was_read(self, apps):
        for row in apps:
            assert row["landing"] in LANDING_VALUES, row["slug"]
            assert "Read:" in row["evidence"] or "read" in row["evidence"].lower(), row["slug"]

    def test_the_four_brain_apps_all_answer_redeploys(self, apps):
        by_slug = {r["slug"]: r for r in apps}
        for slug in ("app-brain", "infra-brain", "open-brain", "code-brain"):
            assert by_slug[slug]["landing"] == LANDING_REDEPLOYS, slug

    def test_the_two_proof_subjects(self, apps):
        by_slug = {r["slug"]: r for r in apps}
        assert by_slug["change-manager"]["landing"] == LANDING_REDEPLOYS
        assert by_slug["orchestrator"]["landing"] == LANDING_INERT

    def test_rejects_a_row_with_no_evidence(self, tmp_path):
        from scripts.backfill_default_branch_landing import load_determination

        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"apps": [{"slug": "x", "landing": "inert", "evidence": " "}]}))
        with pytest.raises(ValueError, match="evidence is required"):
            load_determination(str(bad))

    def test_rejects_an_out_of_vocabulary_landing(self, tmp_path):
        from scripts.backfill_default_branch_landing import load_determination

        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"apps": [{"slug": "x", "landing": "maybe", "evidence": "e"}]}))
        with pytest.raises(ValueError, match="landing must be one of"):
            load_determination(str(bad))
