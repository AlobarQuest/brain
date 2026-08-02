"""Tests for the `repositories` entity (WS-P2.30, schema step).

At this revision nothing reads the table — these pin the schema against the model
and against the canonicalization the read path will use once it does:

  - the migration's frozen landing vocabulary and slug pattern match the model
  - the migration's SQL canonicalization, replayed through Python's `re`, agrees
    with canonical_repo_slug on every shape canonical_repo_slug is tested on
  - both tables carry the two landing CHECKs, under their original names

No DB required (matches the repo's mock-based style). The SQL is additionally
exercised against a real Postgres in-session; see the build report.
"""

import importlib.util
import pathlib
import re

import pytest

from src.brains.app.models import (
    CANONICAL_SLUG_PATTERN,
    LANDING_VALUES,
    App,
    Repository,
)
from src.brains.app.repositories.apps import canonical_repo_slug

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "src/brains/app/migrations/versions/0006_repositories.py"


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("_m0006", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The migration is a frozen copy — pin it to the model
# ---------------------------------------------------------------------------


def test_frozen_vocabulary_matches_the_model(migration):
    """0006 inlines its own copy (migrations must not import the model), so a
    later vocabulary change cannot retroactively rewrite what it asserted."""
    assert migration._LANDING_VALUES == LANDING_VALUES


def test_frozen_slug_pattern_matches_the_model(migration):
    assert migration._OWNER_REPO == CANONICAL_SLUG_PATTERN


# ---------------------------------------------------------------------------
# The SQL canonicalization must agree with the Python one
# ---------------------------------------------------------------------------


def _canonicalize_as_sql_would(migration, value: str) -> str | None:
    """Replay the migration's SQL canonicalization through Python's `re`.

    Postgres `regexp_replace` without the 'g' flag replaces the FIRST match only,
    which is `re.sub(..., count=1)`. This is a mirror, not the real thing —
    Postgres ARE and Python `re` are separate engines — so it catches a typo or a
    one-sided edit, and the migration is additionally run against real Postgres
    before it ships. Where the two could still differ, the SQL is the stricter
    side: a row it cannot parse is absent from `repositories`, which reads as
    `unknown`, never as `inert`.
    """
    s = value.strip()
    for pattern, replacement in (
        migration._STRIP_SCHEME,
        migration._STRIP_SCP,
        migration._STRIP_GIT_SUFFIX,
    ):
        s = re.sub(pattern, replacement, s, count=1)
    m = re.match(migration._OWNER_REPO, s)
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


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
        # unparseable on both sides
        "",
        "   ",
        "no-slash",
        "/",
        "owner/",
        "https://github.com/",
        "https://github.com",
        "ftp://x/",
    ],
)
def test_sql_canonicalization_agrees_with_the_python_one(migration, raw):
    assert _canonicalize_as_sql_would(migration, raw) == canonical_repo_slug(raw)


def test_the_mirror_would_notice_a_one_sided_edit(migration):
    """The agreement test is only worth its runtime if it can fail. Break the
    scheme-stripping step and a URL-shaped value stops canonicalizing."""
    broken = type(migration)("broken")
    broken._STRIP_SCHEME = (r"^nothing-matches-this", "")
    broken._STRIP_SCP = migration._STRIP_SCP
    broken._STRIP_GIT_SUFFIX = migration._STRIP_GIT_SUFFIX
    broken._OWNER_REPO = migration._OWNER_REPO
    url = "https://github.com/AlobarQuest/brain"
    assert canonical_repo_slug(url) == "alobarquest/brain"
    assert _canonicalize_as_sql_would(broken, url) is None


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def _check_names(model) -> set[str]:
    return {c.name for c in model.__table__.constraints if hasattr(c, "sqltext")}


def test_repositories_carries_the_landing_checks():
    names = _check_names(Repository)
    assert "ck_repositories_default_branch_landing" in names
    assert "ck_repositories_default_branch_landing_provenance" in names


def test_apps_no_longer_declares_the_landing_fact():
    """The fact has ONE owner. Leaving a mapped copy on the app would let a
    writer believe they had recorded something the read path never consults —
    two owners of one fact is the duplication this entity exists to remove. The
    database columns 0005 added are dropped by 0007."""
    assert not _check_names(App)
    assert not [c for c in App.__table__.columns if "default_branch_landing" in c.name]


def test_repositories_refuses_a_non_canonical_key():
    """A row keyed 'AlobarQuest/Brain' is unreachable by a query for
    'alobarquest/brain': it would hold a real determination and answer unknown."""
    assert "ck_repositories_canonical_slug" in _check_names(Repository)
    clause = next(
        str(c.sqltext)
        for c in Repository.__table__.constraints
        if getattr(c, "name", None) == "ck_repositories_canonical_slug"
    )
    assert "lower(canonical_slug)" in clause


def test_migration_ddl_and_model_agree_on_the_column_set():
    """The table the migration creates is the table the ORM maps."""
    ddl = MIGRATION.read_text()
    for column in Repository.__table__.columns:
        assert f"{column.name} " in ddl, column.name
