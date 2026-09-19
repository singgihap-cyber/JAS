"""Fase 25 -- validasi urutan tanggal antar-event (services/date_order.py).

Anchor data nyata: temuan Fase 23 (c) -- KW/MD batch `030218-260618-00`
bertanggal 15/6 padahal batch baru ada lewat sortasi 18/6.
"""
import datetime as dt
from decimal import Decimal as D

import pytest

from traceability_engine.enums import BatchType, EventType, QCStage
from traceability_engine.exceptions import EventDateOrderError
from traceability_engine.models import Batch
from traceability_engine.services.date_order import (
    audit_date_order, check_event_date_order,
)
from traceability_engine.services.events import InputSpec, OutputSpec, record_process_event
from traceability_engine.services.qc_md import QCTestInput, record_qc_test
from traceability_engine.services.receiving import ReceivingInput, record_receiving

D18 = dt.date(2026, 6, 18)


def _batch(session, uid, supplier, date=D18, net=D("10")):
    ev = record_receiving(session, ReceivingInput(
        event_date=date, pic_user_id=uid, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_HIJAU, net_quantity=net))
    return session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


def _selfloop(session, uid, b, date, **kw):
    return record_process_event(
        session, event_type=EventType.SUNDRYING, event_date=date, pic_user_id=uid,
        inputs=[InputSpec(b.batch_id, D("1"))],
        outputs=[OutputSpec(D("1"), batch_id=b.batch_id)], **kw)


def test_consistent_order_has_no_violations(session, staff_user, supplier):
    b = _batch(session, staff_user.user_id, supplier)
    _selfloop(session, staff_user.user_id, b, D18)  # tanggal sama diperbolehkan
    _selfloop(session, staff_user.user_id, b, D18 + dt.timedelta(days=2))
    assert audit_date_order(session) == []


def test_real_case_event_before_batch_creation_is_reported(session, staff_user, supplier):
    b = _batch(session, staff_user.user_id, supplier)
    ev = _selfloop(session, staff_user.user_id, b, dt.date(2026, 6, 15))  # 15/6 < 18/6
    [v] = audit_date_order(session)
    assert v.batch_id == b.batch_id and v.event_id == ev.event_id
    assert v.days_early == 3 and v.prior_event_type == "RECEIVING"
    assert v.event_date == dt.date(2026, 6, 15) and v.prior_event_date == D18
    assert "3 hari" in v.message()
    assert audit_date_order(session, batch_id=b.batch_id) == [v]
    assert audit_date_order(session, batch_id=999) == []


def test_default_does_not_block_backdated_event(session, staff_user, supplier):
    """Default = perilaku lama: event tetap terekam (data historis sah)."""
    b = _batch(session, staff_user.user_id, supplier)
    ev = _selfloop(session, staff_user.user_id, b, dt.date(2026, 6, 15))
    assert ev.event_id is not None


def test_strict_blocks_and_persists_nothing(session, staff_user, supplier):
    uid = staff_user.user_id
    b = _batch(session, uid, supplier)
    before = b.current_quantity
    with pytest.raises(EventDateOrderError, match="lebih awal 3 hari"):
        _selfloop(session, uid, b, dt.date(2026, 6, 15), strict_date_order=True)
    assert b.current_quantity == before
    assert audit_date_order(session) == []
    _selfloop(session, uid, b, D18, strict_date_order=True)  # tanggal sama lolos


def test_strict_checks_inputs_of_multi_input_event(session, staff_user, supplier):
    uid = staff_user.user_id
    old = _batch(session, uid, supplier, date=dt.date(2026, 6, 1))
    new = _batch(session, uid, supplier, date=dt.date(2026, 6, 20))
    with pytest.raises(EventDateOrderError) as ei:
        record_process_event(
            session, event_type=EventType.MIXING, event_date=dt.date(2026, 6, 10),
            pic_user_id=uid,
            inputs=[InputSpec(old.batch_id, D("2")), InputSpec(new.batch_id, D("2"))],
            outputs=[OutputSpec(D("4"), batch_id=old.batch_id)], strict_date_order=True)
    assert f"{new.batch_id}" in str(ei.value) or new.batch_number in str(ei.value)
    [v] = check_event_date_order(session, dt.date(2026, 6, 10), [old.batch_id, new.batch_id])
    assert v.batch_id == new.batch_id and v.days_early == 10


def test_void_events_are_ignored(session, staff_user, supplier):
    from traceability_engine.enums import EventStatus
    uid = staff_user.user_id
    b = _batch(session, uid, supplier)
    late = _selfloop(session, uid, b, D18 + dt.timedelta(days=5))
    late.status = EventStatus.VOID
    session.flush()
    _selfloop(session, uid, b, D18 + dt.timedelta(days=1), strict_date_order=True)
    assert audit_date_order(session) == []


def test_qc_via_service_default_unaffected(session, staff_user, supplier):
    """QC (self-loop inspeksi) berlabel tanggal sebelum batch tetap terekam
    dan muncul di audit -- meniru temuan KW/MD 23c."""
    uid = staff_user.user_id
    b = _batch(session, uid, supplier)
    record_qc_test(session, QCTestInput(
        event_date=dt.date(2026, 6, 15), pic_user_id=uid, batch_id=b.batch_id,
        stage=QCStage.RM, ka_1=D("20"), aw=D("0.6")))
    [v] = audit_date_order(session)
    assert v.event_type == "QC_TEST" and v.days_early == 3
