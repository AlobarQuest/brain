import uuid as uuid_mod

import pytest
import sqlalchemy as sa
from fastmcp import FastMCP
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import src.core.db as db_module
from src.core import governance as g


class _Base(DeclarativeBase):
    pass


class _Rec(_Base, g.GovernanceMixin):
    __tablename__ = "recs"
    __table_args__ = g.governance_check_constraints("recs")
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text)
    check: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    category: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_app: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class _UuidRec(_Base, g.GovernanceMixin):
    """A small UUID-PK governed model, alongside int-PK _Rec, so approve/reject/deprecate's
    id coercion is exercised for both PK shapes without depending on app-brain's test suite."""
    __tablename__ = "uuid_recs"
    __table_args__ = g.governance_check_constraints("uuid_recs")
    id: Mapped[uuid_mod.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid_mod.uuid4
    )
    name: Mapped[str] = mapped_column(sa.Text)


@pytest.fixture
def session():
    engine = sa.create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        yield s


def test_mixin_columns_exist_with_defaults(session):
    r = _Rec(name="x")
    session.add(r)
    session.commit()
    session.refresh(r)
    assert r.status == "proposed"
    assert r.authority == "informational"
    assert r.version == 1
    assert r.applicability == {}
    assert r.conflict_kind is None


def test_status_check_constraint_rejects_bad_value(session):
    session.add(_Rec(name="y", status="bogus"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_approver_ok():
    k = "a" * 64
    assert g.approver_ok(k, k) is True
    assert g.approver_ok("b" * 64, k) is False
    assert g.approver_ok(None, k) is False


def _norm(d):
    return g.normalized_check(d)


def test_normalized_check_is_key_order_independent():
    assert _norm({"a": 1, "b": 2}) == _norm({"b": 2, "a": 1})
    assert _norm(None) is None
    assert _norm({}) is None


def test_overlap_signature_skips_when_field_none():
    fields = ("category", "source_app")
    assert g.overlap_signature({"category": "security", "source_app": None}, fields) is None
    got = g.overlap_signature({"category": "security", "source_app": "x"}, fields)
    assert got == ("security", "x")


@pytest.mark.asyncio
async def test_find_conflicts_duplicate_and_overlap():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    chk = {"kind": "forbidden_pattern", "scope": "tracked", "pattern": "P"}
    async with Session() as s:
        # an approved, required record is the only kind that is a conflict TARGET
        s.add(_Rec(name="base", status="approved", authority="required",
                   check=chk, category="security", source_app="app1"))
        await s.commit()
    async with Session() as s:
        dup = await g.find_conflicts(s, _Rec, candidate_check=dict(chk),
                                     overlap_key_fields=("category", "source_app"),
                                     candidate={"category": "security", "source_app": "app1"})
        assert dup is not None and dup.kind == g.CONFLICT_DUPLICATE
        over = await g.find_conflicts(s, _Rec, candidate_check={"kind": "x"},
                                      overlap_key_fields=("category", "source_app"),
                                      candidate={"category": "security", "source_app": "app1"})
        assert over is not None and over.kind == g.CONFLICT_OVERLAP
        none = await g.find_conflicts(s, _Rec, candidate_check={"kind": "y"},
                                      overlap_key_fields=("category", "source_app"),
                                      candidate={"category": "security", "source_app": "OTHER"})
        assert none is None


@pytest.mark.asyncio
async def test_informational_record_is_not_a_target():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    chk = {"kind": "forbidden_pattern", "pattern": "P"}
    async with Session() as s:
        s.add(_Rec(name="info", status="approved", authority="informational", check=chk))
        await s.commit()
    async with Session() as s:
        assert await g.find_conflicts(s, _Rec, candidate_check=dict(chk),
                                      overlap_key_fields=(), candidate={}) is None


@pytest.mark.asyncio
async def test_exclude_id_prevents_self_target():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    chk = {"kind": "forbidden_pattern", "pattern": "P"}
    async with Session() as s:
        r = _Rec(name="self", status="approved", authority="required", check=chk)
        s.add(r)
        await s.commit()
        rec_id = r.id
    async with Session() as s:
        # without exclude_id, the record is its own conflict target (proves
        # the check below isn't just an unrelated miss)
        hit = await g.find_conflicts(s, _Rec, candidate_check=dict(chk),
                                     overlap_key_fields=(), candidate={})
        assert hit is not None and hit.kind == g.CONFLICT_DUPLICATE
        excluded = await g.find_conflicts(s, _Rec, candidate_check=dict(chk),
                                          overlap_key_fields=(), candidate={},
                                          exclude_id=rec_id)
        assert excluded is None


@pytest.mark.asyncio
async def test_proposed_required_is_not_a_target():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    chk = {"kind": "forbidden_pattern", "pattern": "P"}
    async with Session() as s:
        s.add(_Rec(name="proposed", status="proposed", authority="required", check=chk))
        await s.commit()
    async with Session() as s:
        assert await g.find_conflicts(s, _Rec, candidate_check=dict(chk),
                                      overlap_key_fields=(), candidate={}) is None


def test_proposed_defaults_contributor_cannot_auto_approve(monkeypatch):
    monkeypatch.setattr(g, "require_approver", lambda: False)
    d = g.proposed_defaults(
        proposed_by="agent-x", applicability={"category": "security"}, auto_approve=True
    )
    assert d["status"] == "proposed"          # auto_approve ignored without approver key
    assert d["proposed_by"] == "agent-x"
    assert d["authority"] == "informational"


def test_proposed_defaults_approver_auto_approves(monkeypatch):
    monkeypatch.setattr(g, "require_approver", lambda: True)
    d = g.proposed_defaults(proposed_by="devon", applicability={}, auto_approve=True)
    assert d["status"] == "approved"
    assert d["reviewed_by"] == g.APPROVER_IDENTITY
    assert d["reviewed_at"] is not None


def test_finalize_governance_duplicate_cancels_auto_approve():
    d = {"status": "approved", "reviewed_by": "devon", "reviewed_at": "t"}
    g.finalize_governance(d, g.ConflictFlag(g.CONFLICT_DUPLICATE, "dup of #1"))
    assert d["status"] == "proposed"          # never auto-approve over a duplicate
    assert "reviewed_by" not in d and "reviewed_at" not in d
    assert d["conflict_kind"] == "duplicate"


def test_finalize_governance_overlap_is_advisory():
    d = {"status": "approved", "reviewed_by": "devon", "reviewed_at": "t"}
    g.finalize_governance(d, g.ConflictFlag(g.CONFLICT_OVERLAP, "overlaps #1"))
    assert d["status"] == "approved"          # overlap does not block auto-approve
    assert d["conflict_kind"] == "overlap"


# ---------------------------------------------------------------------------
# _coerce_record_id / approve tool — int-PK and UUID-PK id coercion
#
# Regression coverage for the approve/reject/deprecate tools' `id: int | str`
# signature (widened to support AppKnowledge's UUID PK): a stringified int id
# ("5") must still coerce to int for an Integer-PK model (infra/code brains),
# because asyncpg rejects a str bound against an Integer column.
# ---------------------------------------------------------------------------

@pytest.fixture
async def gov_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    yield engine
    await engine.dispose()


def _data(result) -> dict:
    """Unwrap a mcp.call_tool() ToolResult's structured_content, which is typed Optional
    even though every governance tool returns a dict (mirrors tests/brains/test_app.py)."""
    assert result.structured_content is not None
    return result.structured_content


async def test_approve_coerces_stringified_int_id_for_integer_pk(gov_engine, monkeypatch):
    factory = async_sessionmaker(gov_engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([
            _Rec(name="x", status=g.STATUS_PROPOSED),
            _Rec(name="y", status=g.STATUS_PROPOSED),
        ])
        await s.commit()

    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(g, "require_approver", lambda: True)

    mcp = FastMCP("t")
    g.register_governance_tools(mcp, {"rec": _Rec})

    # stringified int id ("1") must coerce to int and approve row 1 (not error)
    stringified = await mcp.call_tool("approve", {"record_type": "rec", "id": "1"})
    assert _data(stringified)["approved"] is True
    assert _data(stringified)["status"] == "approved"

    # native int id still works unchanged
    native = await mcp.call_tool("approve", {"record_type": "rec", "id": 2})
    assert _data(native)["approved"] is True

    async with factory() as s:
        row1 = await s.get(_Rec, 1)
        row2 = await s.get(_Rec, 2)
        assert row1 is not None and row1.status == g.STATUS_APPROVED
        assert row2 is not None and row2.status == g.STATUS_APPROVED

    # a non-numeric string returns the shared invalid_id error, same shape as a bad UUID
    bad = await mcp.call_tool("approve", {"record_type": "rec", "id": "not-a-number"})
    assert _data(bad) == {
        "error": "invalid_id", "record_type": "rec", "id": "not-a-number"
    }


async def test_approve_coerces_uuid_string_id_for_uuid_pk(gov_engine, monkeypatch):
    factory = async_sessionmaker(gov_engine, expire_on_commit=False)
    async with factory() as s:
        rec = _UuidRec(name="u", status=g.STATUS_PROPOSED)
        s.add(rec)
        await s.commit()
        await s.refresh(rec)
        rec_id = rec.id

    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(g, "require_approver", lambda: True)

    mcp = FastMCP("t")
    g.register_governance_tools(mcp, {"uuid_rec": _UuidRec})

    ok = await mcp.call_tool("approve", {"record_type": "uuid_rec", "id": str(rec_id)})
    assert _data(ok)["approved"] is True

    async with factory() as s:
        row = await s.get(_UuidRec, rec_id)
        assert row is not None and row.status == g.STATUS_APPROVED

    bad = await mcp.call_tool("approve", {"record_type": "uuid_rec", "id": "not-a-uuid"})
    assert _data(bad) == {
        "error": "invalid_id", "record_type": "uuid_rec", "id": "not-a-uuid"
    }


def test_coerce_record_id_int_against_uuid_pk_does_not_raise():
    """An int id passed for a UUID-PK model must not raise a raw AttributeError
    (session.get() on a Uuid column calling .hex on a plain int) — it should either
    coerce cleanly (if the int happens to be a valid UUID literal) or raise ValueError
    so the caller's invalid_id path handles it."""
    with pytest.raises(ValueError):
        g._coerce_record_id(_UuidRec, 5)


def test_coerce_record_id_uuid_instance_against_uuid_pk_passes_through():
    u = uuid_mod.uuid4()
    assert g._coerce_record_id(_UuidRec, u) == u


async def test_approve_int_id_against_uuid_pk_returns_invalid_id_not_raw_exception(
    gov_engine, monkeypatch
):
    factory = async_sessionmaker(gov_engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(g, "require_approver", lambda: True)

    mcp = FastMCP("t")
    g.register_governance_tools(mcp, {"uuid_rec": _UuidRec})

    result = await mcp.call_tool("approve", {"record_type": "uuid_rec", "id": 5})
    assert _data(result) == {"error": "invalid_id", "record_type": "uuid_rec", "id": 5}
