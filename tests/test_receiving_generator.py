import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType
from traceability_engine.exceptions import BatchNumberConflictError
from traceability_engine.models import Batch
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.stock import reconcile_batch

D = dt.date(2026, 9, 21)


def _recv(session, user, supplier, **kw):
    base = dict(event_date=D, pic_user_id=user.user_id, supplier_id=supplier.supplier_id,
                batch_type=BatchType.RAW_KERING, net_quantity=Decimal("10"),
                jenis_code="02", grade_code="01")
    base.update(kw)
    return record_receiving(session, ReceivingInput(**base))


def test_generates_number_from_supplier_master(session, staff_user, supplier):
    _recv(session, staff_user, supplier)
    b = session.query(Batch).one()
    assert b.batch_number == "0201024-260921-00"
    assert (b.jenis_code, b.grade_code, b.supplier_code) == ("02", "01", "024")
    assert b.receiving_date == D and b.process_code == "00"


def test_hijau_defaults_to_grade_00_and_rejects_other(session, staff_user, supplier):
    _recv(session, staff_user, supplier, batch_type=BatchType.RAW_HIJAU, grade_code=None, jenis_code="01")
    assert session.query(Batch).one().batch_number == "0100024-260921-00"
    with pytest.raises(ValueError):
        _recv(session, staff_user, supplier, batch_type=BatchType.RAW_HIJAU, grade_code="01")


def test_kering_requires_grade(session, staff_user, supplier):
    with pytest.raises(ValueError):
        _recv(session, staff_user, supplier, grade_code=None)


def test_legacy_jenis_rejected(session, staff_user, supplier):
    with pytest.raises(ValueError):
        _recv(session, staff_user, supplier, jenis_code="03")


def test_same_day_second_receiving_merges_into_one_batch(session, staff_user, supplier):
    _recv(session, staff_user, supplier, net_quantity=Decimal("10"))
    _recv(session, staff_user, supplier, net_quantity=Decimal("5"), off_spec_qty=Decimal("1"))
    b = session.query(Batch).one()
    assert b.current_quantity == Decimal("14")  # 10 + 5 - 1 off-spec
    reconcile_batch(session, b.batch_id)


def test_different_grade_or_date_makes_new_batch(session, staff_user, supplier):
    _recv(session, staff_user, supplier)
    _recv(session, staff_user, supplier, grade_code="02")
    _recv(session, staff_user, supplier, event_date=dt.date(2026, 9, 22))
    assert session.query(Batch).count() == 3


def test_conflict_when_existing_batch_already_processed(session, staff_user, supplier):
    _recv(session, staff_user, supplier)
    b = session.query(Batch).one()
    b.status = BatchStatus.CONSUMED
    session.flush()
    with pytest.raises(BatchNumberConflictError):
        _recv(session, staff_user, supplier)


def test_manual_number_still_wins_and_does_not_merge(session, staff_user, supplier):
    _recv(session, staff_user, supplier, batch_number="030224-260221-00")
    assert session.query(Batch).one().batch_number == "030224-260221-00"


def test_conflict_when_existing_batch_has_been_processed(session, staff_user, supplier):
    from traceability_engine.services.qc_md import QCTestInput, record_qc_test
    _recv(session, staff_user, supplier)
    b = session.query(Batch).one()
    record_qc_test(session, QCTestInput(event_date=D, pic_user_id=staff_user.user_id,
                                        batch_id=b.batch_id, stage="RM"))
    with pytest.raises(BatchNumberConflictError):
        _recv(session, staff_user, supplier)
