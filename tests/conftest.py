import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from traceability_engine.enums import UserRole
from traceability_engine.models import Base, Supplier, User


@pytest.fixture()
def engine():
    # SQLite in-memory for tests; PostgreSQL in production. No code
    # difference -- only SQLAlchemy-portable types are used (PROJECT_STATUS.md).
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as sess:
        yield sess


@pytest.fixture()
def production_manager(session):
    user = User(name="Robiah", role=UserRole.PRODUCTION_MANAGER)
    session.add(user)
    session.flush()
    return user


@pytest.fixture()
def staff_user(session):
    user = User(name="Wakhidah", role=UserRole.STAFF)
    session.add(user)
    session.flush()
    return user


@pytest.fixture()
def supplier(session):
    s = Supplier(supplier_code="024", name="WARDOYO")
    session.add(s)
    session.flush()
    return s


TODAY = dt.date(2026, 9, 14)
Q = Decimal
