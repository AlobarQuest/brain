"""Tests for the default-branch landing fact (WS-P2.29, moved to the repository
by WS-P2.30):
  - the pure answer-builder (aggregate_landing) for one repository
  - GET /api/apps/default-branch-landing behavior, with the repo mocked
  - the read-only key: it reaches the read paths and nothing else
  - the determination file's own shape, and its vocabulary against the model

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
from src.brains.app.repositories.apps import aggregate_landing, canonical_repo_slug

KEY = "a" * 64
READ_KEY = "b" * 64
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

BRAIN = "AlobarQuest/brain"
BRAINS = ["app-brain", "code-brain", "infra-brain", "open-brain"]


def _repository(landing, evidence="read the workflow"):
    """A repositories row as the repository layer projects it."""
    return {
        "landing": landing,
        "determined_at": "2026-08-02T00:00:00+00:00" if landing else None,
        "evidence": evidence if landing else None,
    }


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


class TestAggregateLanding:
    def test_an_unregistered_repository_is_unknown_not_inert(self):
        """App Brain not knowing a repository is never evidence merging is safe."""
        out = aggregate_landing("AlobarQuest/nope", None, [])
        assert out["landing"] == LANDING_UNKNOWN
        assert out["reason"] == "no_app_record"
        assert out["apps"] == []

    def test_a_redeploying_repository(self):
        out = aggregate_landing(
            "AlobarQuest/change-manager", _repository("redeploys"), ["change-manager"]
        )
        assert out["landing"] == LANDING_REDEPLOYS
        assert out["reason"] is None

    def test_an_inert_repository(self):
        out = aggregate_landing("AlobarQuest/orchestrator", _repository("inert"), ["orchestrator"])
        assert out["landing"] == LANDING_INERT
        assert out["reason"] is None

    def test_a_registered_but_unassessed_repository_is_unknown(self):
        out = aggregate_landing("AlobarQuest/x", _repository(None), ["x"])
        assert out["landing"] == LANDING_UNKNOWN
        assert out["reason"] == "not_assessed"
        # and it is NOT the same answer as an assessed non-deploying repository
        assert (
            aggregate_landing("AlobarQuest/y", _repository("inert"), ["y"])["landing"]
            == LANDING_INERT
        )

    def test_an_app_with_no_repository_row_is_unassessed_not_unregistered(self):
        """`no_app_record` must keep meaning "never heard of it". An app we know
        perfectly well, whose repository nobody assessed, is `not_assessed`."""
        out = aggregate_landing("AlobarQuest/x", None, ["x"])
        assert out["landing"] == LANDING_UNKNOWN
        assert out["reason"] == "not_assessed"

    def test_four_brains_one_repository_one_answer(self):
        """AlobarQuest/brain feeds four running services from one ci.yml. WS-P2.29
        stored four copies of that answer and folded them; there is now one."""
        out = aggregate_landing(BRAIN, _repository("redeploys"), BRAINS)
        assert out["landing"] == LANDING_REDEPLOYS
        assert [a["slug"] for a in out["apps"]] == BRAINS
        assert out["matched_apps"] == 4
        # every app it feeds reports the determination that governs it
        assert {a["default_branch_landing"] for a in out["apps"]} == {LANDING_REDEPLOYS}

    def test_a_repository_with_no_apps_still_has_an_answer(self):
        """The workstream's point: the factory targets repositories that deploy
        nothing, and an app registry structurally cannot answer for them."""
        out = aggregate_landing("AlobarQuest/intent-packages", _repository("inert"), [])
        assert out["landing"] == LANDING_INERT
        assert out["reason"] is None
        assert out["matched_apps"] == 0
        # provenance must be reachable when there are no apps to carry it
        assert out["evidence"] == "read the workflow"
        assert out["determined_at"] is not None

    def test_provenance_is_absent_exactly_when_the_answer_is_unknown(self):
        """An `unknown` carries no determination, so it must carry no date or
        evidence either — a caller must not read staleness into a non-answer."""
        out = aggregate_landing("AlobarQuest/x", _repository(None), ["x"])
        assert out["determined_at"] is None and out["evidence"] is None


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_landing_in_clause_has_no_trailing_comma():
    """A one-element tuple's repr renders `IN ('x',)`, a Postgres syntax error."""
    assert landing_in_clause() == "default_branch_landing IN ('redeploys', 'inert')"
    assert ",)" not in landing_in_clause()


def test_migration_frozen_vocabulary_matches_the_model():
    """0005 inlines a frozen copy (migrations must not import the model). Its
    columns are dead after the cut-over and 0007 drops them; while they exist,
    the constraint they carry must still name the vocabulary in force."""
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


class TestCanonicalRepoSlug:
    """The lookup key. A repository row is stored under its canonical slug and a
    query is canonicalized before it, so every shape the estate writes reaches
    the same record — and an app stored in an odd shape still counts toward the
    denominator a caller weighs `inert` against."""

    @pytest.mark.parametrize(
        "raw",
        [
            "AlobarQuest/brain",
            "alobarquest/BRAIN",
            "  AlobarQuest/brain  ",
            "AlobarQuest/brain/",
            "AlobarQuest/brain.git",
            "https://github.com/AlobarQuest/brain",
            "https://github.com/AlobarQuest/brain.git",
            "git@github.com:AlobarQuest/brain.git",
        ],
    )
    def test_every_stored_shape_canonicalizes_to_one_slug(self, raw):
        assert canonical_repo_slug(raw) == "alobarquest/brain"

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "   ",
            "no-slash",
            "/",
            "owner/",
            # A scheme we failed to strip still splits into two segments, and
            # would otherwise canonicalize to the nonsense slug "https:/github.com".
            "https://github.com/",
            "https://github.com",
            "ftp://x/",
        ],
    )
    def test_unparseable_is_none(self, raw):
        assert canonical_repo_slug(raw) is None

    def test_every_shape_reaches_the_same_repository_record(self):
        """One repository written four ways is one lookup key, so a determination
        recorded against any of them is served for all of them."""
        shapes = [BRAIN, "alobarquest/brain", "https://github.com/AlobarQuest/brain.git", BRAIN[:]]
        assert len({canonical_repo_slug(s) for s in shapes}) == 1

    def test_inert_is_never_returned_without_its_denominator(self):
        """`inert` ranges over what the estate RECORDED, so a caller must always
        see how many apps were matched."""
        out = aggregate_landing("AlobarQuest/orchestrator", _repository("inert"), ["orchestrator"])
        assert out["landing"] == LANDING_INERT
        assert out["matched_apps"] == 1
        assert aggregate_landing("AlobarQuest/nope", None, [])["matched_apps"] == 0


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
    rec = aggregate_landing(
        "AlobarQuest/change-manager", _repository("redeploys"), ["change-manager"]
    )
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
    _patch_repo(monkeypatch, aggregate_landing("AlobarQuest/nope", None, []))
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
    rec = aggregate_landing("AlobarQuest/orchestrator", _repository("inert"), ["orchestrator"])
    _patch_repo(monkeypatch, rec)
    async with await _client(env_setup) as client:
        resp = await client.get(
            "/api/apps/default-branch-landing",
            params={"github_repo": "AlobarQuest/orchestrator"},
            headers={"x-brain-key": READ_KEY},
        )
    assert resp.status_code == 200
    assert resp.json()["landing"] == "inert"


@pytest.mark.parametrize("method,path", [("POST", "/mcp/"), ("GET", "/mcp/"), ("POST", "/mcp")])
async def test_read_key_is_refused_on_the_mcp_surface(env_setup, monkeypatch, method, path):
    """The whole point: a read key cannot reach any write tool. Covers GET as
    well as POST — a POST-only test proves nothing about /mcp specifically,
    because the read key cannot POST anywhere at all."""
    _patch_repo(monkeypatch, None)
    async with await _client(env_setup) as client:
        resp = await client.request(
            method,
            path,
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
# The write tool
# ---------------------------------------------------------------------------


class _RecordingRepo:
    """Captures what the tools asked to write, without a DB."""

    last: dict = {}
    registered: list = []

    def __init__(self, session):
        pass

    async def update_app(self, slug, **fields):
        _RecordingRepo.last = {"slug": slug, **fields}
        return type("A", (), {"slug": slug, "name": "n", "status": "active"})()

    async def ensure_repository(self, github_repo):
        _RecordingRepo.registered.append(github_repo)
        return type("R", (), {"canonical_slug": github_repo.lower()})()

    async def record_repository_landing(self, github_repo, landing, evidence, determined_at):
        _RecordingRepo.last = {
            "github_repo": github_repo,
            "default_branch_landing": landing,
            "default_branch_landing_evidence": evidence,
            "default_branch_landing_determined_at": determined_at,
        }
        return type("R", (), {"canonical_slug": github_repo.lower()})()


class _CommitSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


async def _tools(monkeypatch):
    from fastmcp import FastMCP

    import src.brains.app.tools.apps as tools_mod

    monkeypatch.setattr(tools_mod, "get_session_factory", lambda: lambda: _CommitSession())
    monkeypatch.setattr(tools_mod, "AppRepository", _RecordingRepo)
    mcp = FastMCP("t")
    tools_mod.register_app_tools(mcp)
    return {t.name: t for t in await mcp.list_tools()}


async def _call(monkeypatch, name, **kwargs):
    tools = await _tools(monkeypatch)
    result = await tools[name].run(kwargs)
    return json.loads(result.content[0].text)


REPO = "AlobarQuest/x"


async def test_record_rejects_out_of_vocabulary_landing(monkeypatch):
    out = await _call(
        monkeypatch,
        "record_default_branch_landing",
        github_repo=REPO,
        landing="maybe",
        evidence="e",
    )
    assert "invalid_params" in out["error"]


async def test_record_rejects_blank_evidence(monkeypatch):
    out = await _call(
        monkeypatch,
        "record_default_branch_landing",
        github_repo=REPO,
        landing="inert",
        evidence="   ",
    )
    assert "evidence is required" in out["error"]


@pytest.mark.parametrize("bad", ["not-a-slug", "https://github.com/", "   "])
async def test_record_refuses_a_repo_reference_the_lookup_cannot_match(monkeypatch, bad):
    """Stored under a key nobody will ask for, while the key they do ask returns
    no_app_record — a determination that exists and is unreachable."""
    out = await _call(
        monkeypatch,
        "record_default_branch_landing",
        github_repo=bad,
        landing="inert",
        evidence="Read: the workflow file.",
    )
    assert "github_repo must be" in out["error"]


async def test_record_stamps_determined_at_server_side(monkeypatch):
    out = await _call(
        monkeypatch,
        "record_default_branch_landing",
        github_repo=REPO,
        landing="redeploys",
        evidence="Read: the workflow file.",
    )
    assert out["default_branch_landing"] == "redeploys"
    assert out["canonical_slug"] == "alobarquest/x"
    assert _RecordingRepo.last["default_branch_landing_determined_at"] is not None
    assert _RecordingRepo.last["default_branch_landing_evidence"] == "Read: the workflow file."


async def test_recording_registers_a_repository_with_no_application(monkeypatch):
    """The workstream's point: this is how a factory-targetable, not-deployed
    repository enters the registry. There is no separate register step, because
    a registered-but-unassessed row answers the same `unknown` as no row."""
    out = await _call(
        monkeypatch,
        "record_default_branch_landing",
        github_repo="AlobarQuest/intent-packages",
        landing="inert",
        evidence="Read: the workflow files, the webhook list, the Pages project list.",
    )
    assert out["default_branch_landing"] == "inert"
    assert _RecordingRepo.last["github_repo"] == "AlobarQuest/intent-packages"


async def test_a_determination_can_be_retracted_to_unknown(monkeypatch):
    """Without this the fail-closed state is reachable only until someone first
    writes, so a determination read against the wrong repository could be
    corrected only to a claim you cannot support."""
    out = await _call(
        monkeypatch,
        "record_default_branch_landing",
        github_repo=REPO,
        landing="unknown",
        evidence="",
    )
    assert out["default_branch_landing"] == "unknown"
    assert out["determined_at"] is None
    assert _RecordingRepo.last["default_branch_landing"] is None
    assert _RecordingRepo.last["default_branch_landing_determined_at"] is None
    assert _RecordingRepo.last["default_branch_landing_evidence"] is None


@pytest.mark.parametrize("bad", ["not-a-slug", "https://github.com/", "   "])
async def test_update_app_refuses_a_github_repo_the_lookup_cannot_match(monkeypatch, bad):
    out = await _call(monkeypatch, "update_app", slug="x", github_repo=bad)
    assert "github_repo must be" in out["error"]


async def test_update_app_accepts_a_canonical_slug(monkeypatch):
    out = await _call(monkeypatch, "update_app", slug="x", github_repo="AlobarQuest/brain")
    assert out.get("updated") == ["github_repo"]


async def test_declaring_an_apps_repository_registers_it(monkeypatch):
    """Otherwise an app the estate knows perfectly well answers `no_app_record`
    — "never heard of this repository" — when the truth is `not_assessed`."""
    _RecordingRepo.registered = []
    await _call(monkeypatch, "update_app", slug="x", github_repo="AlobarQuest/brain")
    assert _RecordingRepo.registered == ["AlobarQuest/brain"]


async def test_updating_something_else_does_not_register_anything(monkeypatch):
    _RecordingRepo.registered = []
    await _call(monkeypatch, "update_app", slug="x", status="active")
    assert _RecordingRepo.registered == []


def test_get_app_serves_unknown_not_null():
    """The REST route argues unknown must appear on the wire as a value; the MCP
    surface must not then serve a falsy null that invites `if not x: proceed`.
    An app with no github_repo has no repository, so it has no answer."""
    from src.brains.app.tools.apps import serialize_app_profile

    class _App:
        slug = name = "x"
        description = tech_stack = repo_path = deployment_url = github_repo = None
        environments = []
        status = "active"
        tags = []
        onboarding_status = "pending"
        last_onboarded_at = created_at = None

    profile = serialize_app_profile(_App(), coverage={})
    assert profile["default_branch_landing"] == LANDING_UNKNOWN
    assert profile["default_branch_landing_determined_at"] is None


def test_get_app_reports_its_repositorys_determination():
    from src.brains.app.tools.apps import serialize_app_profile

    class _App:
        slug = name = "app-brain"
        description = tech_stack = repo_path = deployment_url = None
        github_repo = BRAIN
        environments = []
        status = "active"
        tags = []
        onboarding_status = "complete"
        last_onboarded_at = created_at = None

    landing = aggregate_landing(BRAIN, _repository("redeploys"), BRAINS)
    profile = serialize_app_profile(_App(), coverage={}, landing=landing)
    assert profile["default_branch_landing"] == LANDING_REDEPLOYS
    assert profile["default_branch_landing_evidence"] == "read the workflow"


# ---------------------------------------------------------------------------
# The determination file — repositories with NO application
# ---------------------------------------------------------------------------


class TestDeterminationFile:
    @pytest.fixture
    def repositories(self):
        from scripts.record_repository_landing import load_determination

        return load_determination()

    def test_loads_and_validates(self, repositories):
        """Only the app-less repositories live here; every repository that feeds
        a registered app was backfilled from its app by migration 0006."""
        assert len(repositories) == 2

    def test_every_row_carries_evidence_naming_what_was_read(self, repositories):
        """`"Read:" in e or "read" in e.lower()` would be the natural spelling and
        is worthless: the second clause subsumes the first, so it only enforces
        that the free text contains the substring "read" — which a repo named
        `threads` satisfies. Require the explicit clause."""
        for row in repositories:
            assert row["landing"] in LANDING_VALUES, row["github_repo"]
            assert "Read:" in row["evidence"], row["github_repo"]

    def test_the_two_factory_targetable_repositories(self, repositories):
        by_repo = {r["github_repo"]: r for r in repositories}
        assert by_repo["AlobarQuest/intent-packages"]["landing"] == LANDING_INERT
        assert by_repo["AlobarQuest/project-standards"]["landing"] == LANDING_INERT

    def test_evidence_names_all_three_trigger_mechanisms(self, repositories):
        """Reading CI alone gets 3 of 10 wrong (WS-P2.29). An `inert` determined
        from one mechanism is an unfounded permission."""
        for row in repositories:
            e = row["evidence"].lower()
            assert "workflow" in e, row["github_repo"]
            assert "webhook" in e, row["github_repo"]
            assert "pages" in e and "coolify" in e, row["github_repo"]

    def test_rejects_a_row_with_no_evidence(self, tmp_path):
        from scripts.record_repository_landing import load_determination

        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {"repositories": [{"github_repo": "o/r", "landing": "inert", "evidence": " "}]}
            )
        )
        with pytest.raises(ValueError, match="evidence is required"):
            load_determination(str(bad))

    def test_rejects_an_out_of_vocabulary_landing(self, tmp_path):
        from scripts.record_repository_landing import load_determination

        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {"repositories": [{"github_repo": "o/r", "landing": "maybe", "evidence": "e"}]}
            )
        )
        with pytest.raises(ValueError, match="landing must be one of"):
            load_determination(str(bad))

    def test_rejects_a_reference_the_lookup_cannot_match(self, tmp_path):
        from scripts.record_repository_landing import load_determination

        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {"repositories": [{"github_repo": "nope", "landing": "inert", "evidence": "e"}]}
            )
        )
        with pytest.raises(ValueError, match="must be 'owner/repo'"):
            load_determination(str(bad))

    def test_every_declared_repository_is_canonical(self, repositories):
        for row in repositories:
            assert canonical_repo_slug(row["github_repo"]) == row["github_repo"].lower()

    def test_evidence_carries_no_credential_shaped_text(self, repositories):
        """Evidence is unvalidated free text and is served to the READ-ONLY key,
        the estate's lowest-privilege credential. Nothing secret belongs in it."""
        for row in repositories:
            e = row["evidence"].lower()
            for banned in ("secret_github", "bearer ", "password", "token="):
                assert banned not in e, f"{row['github_repo']}: evidence contains {banned!r}"
