import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
