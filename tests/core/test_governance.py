import pytest
import sqlalchemy as sa
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
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()


def test_approver_ok():
    k = "a" * 64
    assert g.approver_ok(k, k) is True
    assert g.approver_ok("b" * 64, k) is False
    assert g.approver_ok(None, k) is False
