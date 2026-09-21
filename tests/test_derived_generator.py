"""Fase 32 -- generator nomor batch turunan (PP 01/02/03/04)."""
import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType
from traceability_engine.exceptions import BatchNumberConflictError
from traceability_engine.models import Batch
from traceability_engine.services.mixing import MixingInput, MixingSource, record_mixing
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.rework import ReworkInput, record_rework
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.services.stock import reconcile_batch

RECV = dt.date(2026, 9, 1)
D = dt.date(2026, 9, 21)


def _recv(session, user, supplier, jenis="02", grade="01", qty="100", **kw):
    ev = record_receiving(session, ReceivingInput(
        event_date=kw.pop("event_date", RECV), pic_user_id=user.user_id,
        supplier_id=supplier.supplier_id, batch_type=BatchType.RAW_KERING,
        net_quantity=Decimal(qty), jenis_code=jenis, grade_code=grade, **kw))
    return session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


def _numbers(session):
    return sorted(b.batch_number for b in session.query(Batch) if b.batch_number)


def test_downgrade_uses_source_date_supplier_and_grade(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)  # 0201024-260901-00
    record_sortation(session, SortationInput(
        event_date=D, pic_user_id=staff_user.user_id, batch_id=src.batch_id,
        eg_qty=Decimal("40"), process_code="02", auto_batch_number=True))
    out = session.query(Batch).filter(Batch.process_code == "02").one()
    assert out.batch_number == "0202024-260901-02"
    assert (out.jenis_code, out.grade_code, out.supplier_code) == ("02", "02", "024")
    assert out.receiving_date == RECV


def test_upgrade_pp01(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier, grade="02")
    record_sortation(session, SortationInput(
        event_date=D, pic_user_id=staff_user.user_id, batch_id=src.batch_id,
        gourmet_qty=Decimal("10"), process_code="01", auto_batch_number=True))
    assert "0201024-260901-01" in _numbers(session)


def test_sortation_original_pp00_not_auto(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_sortation(session, SortationInput(
            event_date=D, pic_user_id=staff_user.user_id, batch_id=src.batch_id,
            eg_qty=Decimal("10"), process_code="00", auto_batch_number=True))


def test_typed_number_wins_over_auto(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)
    record_sortation(session, SortationInput(
        event_date=D, pic_user_id=staff_user.user_id, batch_id=src.batch_id,
        eg_qty=Decimal("10"), process_code="02", auto_batch_number=True,
        eg_batch_number="0202024-260921-02"))
    assert "0202024-260921-02" in _numbers(session)


def test_legacy_jenis_carried_and_override_normalizes(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)
    src.jenis_code = "03"  # sumber legacy
    session.flush()
    record_rework(session, ReworkInput(
        event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("10"), batch_id=src.batch_id,
        eg_qty=Decimal("10"), auto_batch_number=True))
    assert "0302024-260901-04" in _numbers(session)  # AA 03 dibawa apa adanya
    record_rework(session, ReworkInput(
        event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("10"), batch_id=src.batch_id,
        gourmet_qty=Decimal("10"), auto_batch_number=True, jenis_code="02"))
    assert "0201024-260901-04" in _numbers(session)  # staf memilih 02
    with pytest.raises(ValueError):  # override wajib 01/02
        record_rework(session, ReworkInput(
            event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("1"), batch_id=src.batch_id,
            ep_qty=Decimal("1"), auto_batch_number=True, jenis_code="03"))


def test_same_number_merges_and_ledger_reconciles(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)
    for q in ("10", "5"):
        record_rework(session, ReworkInput(
            event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal(q), batch_id=src.batch_id,
            eg_qty=Decimal(q), auto_batch_number=True))
    out = session.query(Batch).filter(Batch.process_code == "04").one()
    assert out.current_quantity == Decimal("15")
    reconcile_batch(session, out.batch_id)


def test_merge_refused_when_target_already_processed(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)
    record_rework(session, ReworkInput(
        event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("10"), batch_id=src.batch_id,
        eg_qty=Decimal("10"), auto_batch_number=True))
    out = session.query(Batch).filter(Batch.process_code == "04").one()
    # hasil dipakai sebagai input proses lanjut
    record_rework(session, ReworkInput(
        event_date=D, pic_user_id=staff_user.user_id, batch_id=out.batch_id,
        starting_qty=Decimal("4"), gourmet_qty=Decimal("4"),
        auto_batch_number=True, jenis_code="01"))
    with pytest.raises(BatchNumberConflictError):
        record_rework(session, ReworkInput(
            event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("3"), batch_id=src.batch_id,
            eg_qty=Decimal("3"), auto_batch_number=True))


def test_derived_number_equal_to_source_is_conflict(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)
    record_rework(session, ReworkInput(
        event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("10"), batch_id=src.batch_id,
        eg_qty=Decimal("10"), auto_batch_number=True))
    out = session.query(Batch).filter(Batch.process_code == "04").one()
    with pytest.raises(BatchNumberConflictError):  # rework EG dari rework EG -> nomor sama
        record_rework(session, ReworkInput(
            event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("5"), batch_id=out.batch_id,
            eg_qty=Decimal("5"), auto_batch_number=True))


def test_source_without_date_or_supplier_needs_manual(session, staff_user, supplier):
    src = _recv(session, staff_user, supplier)
    src.receiving_date = None
    session.flush()
    with pytest.raises(ValueError):
        record_rework(session, ReworkInput(
            event_date=D, pic_user_id=staff_user.user_id, starting_qty=Decimal("1"), batch_id=src.batch_id,
            eg_qty=Decimal("1"), auto_batch_number=True))


# ------------------------------------------------------------------- mixing
def _mix(session, user, ids, **kw):
    base = dict(event_date=D, pic_user_id=user.user_id,
                sources=[MixingSource(batch_id=i, quantity=Decimal("10")) for i in ids],
                final_qty=Decimal("19"), product_description="GOURMET",
                jenis_code="02", grade_code="01", auto_batch_number=True)
    base.update(kw)
    return record_mixing(session, MixingInput(**base))


def test_mixing_number_uses_mixing_date_and_supplier_000(session, staff_user, supplier):
    a = _recv(session, staff_user, supplier)
    b = _recv(session, staff_user, supplier, grade="02", event_date=dt.date(2026, 9, 2))
    _mix(session, staff_user, [a.batch_id, b.batch_id])
    m = session.query(Batch).filter(Batch.process_code == "03").one()
    assert m.batch_number == "0201000-260921-03"
    assert m.receiving_date == D and m.supplier_code == "000"


def test_mixing_rejects_mixed_jenis_but_legacy_03_equals_planifolia(session, staff_user, supplier):
    a = _recv(session, staff_user, supplier, jenis="01")
    b = _recv(session, staff_user, supplier, jenis="02", grade="02")
    with pytest.raises(ValueError):
        _mix(session, staff_user, [a.batch_id, b.batch_id])
    b.jenis_code = "03"  # legacy Planifolia
    c = _recv(session, staff_user, supplier, jenis="02", grade="03")
    _mix(session, staff_user, [b.batch_id, c.batch_id])
    assert "0201000-260921-03" in _numbers(session)


def test_mixing_requires_jenis_and_grade(session, staff_user, supplier):
    a = _recv(session, staff_user, supplier)
    b = _recv(session, staff_user, supplier, grade="02")
    with pytest.raises(ValueError):
        _mix(session, staff_user, [a.batch_id, b.batch_id], jenis_code=None)


def test_two_mixings_same_day_same_grade_merge(session, staff_user, supplier):
    a = _recv(session, staff_user, supplier)
    b = _recv(session, staff_user, supplier, grade="02")
    c = _recv(session, staff_user, supplier, grade="03")
    d = _recv(session, staff_user, supplier, grade="04")
    _mix(session, staff_user, [a.batch_id, b.batch_id])
    _mix(session, staff_user, [c.batch_id, d.batch_id], final_qty=Decimal("20"))
    m = session.query(Batch).filter(Batch.process_code == "03").one()
    assert m.current_quantity == Decimal("39")
    reconcile_batch(session, m.batch_id)


def test_mixing_without_auto_unchanged(session, staff_user, supplier):
    a = _recv(session, staff_user, supplier)
    b = _recv(session, staff_user, supplier, grade="02")
    _mix(session, staff_user, [a.batch_id, b.batch_id], auto_batch_number=False,
         jenis_code=None, grade_code=None)
    m = session.query(Batch).filter(Batch.process_code == "03").one()
    assert m.batch_number is None
