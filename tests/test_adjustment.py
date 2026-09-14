import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import AuditAction, BatchStatus, BatchType, EventType
from traceability_engine.exceptions import InvalidEventStructureError, UnauthorizedAdjustmentError
from traceability_engine.models import AuditLog, Batch
from traceability_engine.services.adjustment import mark_batch_rejected, mark_batch_superseded, record_adjustment
from traceability_engine.services.events import OutputSpec, NewBatchSpec, record_process_event

TODAY = dt.date(2026, 9, 14)


def _receive(session, staff_user, supplier, qty):
    event = record_process_event(
        session,
        event_type=EventType.RECEIVING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[],
        outputs=[
            OutputSpec(
                quantity=qty,
                new_batch=NewBatchSpec(batch_type=BatchType.RAW_KERING, supplier_id=supplier.supplier_id),
            )
        ],
    )
    return [l.batch_id for l in event.links][0]


# --- TEST_CASES.md #13: Unauthorized stock adjustment is rejected ---------

def test_non_production_manager_cannot_adjust(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, Decimal("100.000"))
    with pytest.raises(UnauthorizedAdjustmentError):
        record_adjustment(
            session,
            batch_id=batch_id,
            new_quantity=Decimal("90.000"),
            actor_user_id=staff_user.user_id,
            notes="stok opname bulanan",
            event_date=TODAY,
        )
    # balance must be untouched
    assert session.get(Batch, batch_id).current_quantity == Decimal("100.000")


def test_adjustment_requires_a_reason(session, production_manager, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, Decimal("100.000"))
    with pytest.raises(InvalidEventStructureError):
        record_adjustment(
            session,
            batch_id=batch_id,
            new_quantity=Decimal("90.000"),
            actor_user_id=production_manager.user_id,
            notes="   ",
            event_date=TODAY,
        )


def test_production_manager_can_adjust(session, production_manager, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, Decimal("100.000"))
    event = record_adjustment(
        session,
        batch_id=batch_id,
        new_quantity=Decimal("92.500"),
        actor_user_id=production_manager.user_id,
        notes="Selisih stok opname fisik gudang",
        event_date=TODAY,
    )
    assert event.event_type == EventType.ADJUSTMENT
    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("92.500")


# --- TEST_CASES.md #14: Audit log records critical changes ----------------

def test_adjustment_writes_audit_log(session, production_manager, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, Decimal("100.000"))
    record_adjustment(
        session,
        batch_id=batch_id,
        new_quantity=Decimal("95.000"),
        actor_user_id=production_manager.user_id,
        notes="Koreksi timbangan",
        event_date=TODAY,
    )
    log = session.query(AuditLog).filter_by(entity_type="Batch", entity_id=batch_id).one()
    assert log.action == AuditAction.ADJUSTMENT_APPROVED
    assert log.before_value == "100.000"
    assert log.after_value == "95.000"
    assert log.actor_user_id == production_manager.user_id


def test_reject_is_manual_and_audited(session, production_manager, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, Decimal("100.000"))
    batch = mark_batch_rejected(
        session, batch_id=batch_id, actor_user_id=production_manager.user_id, reason="AW terlalu tinggi"
    )
    assert batch.status == BatchStatus.REJECTED
    log = session.query(AuditLog).filter_by(entity_type="Batch", entity_id=batch_id).one()
    assert log.action == AuditAction.REJECTED
    assert "AW terlalu tinggi" in log.after_value


def test_supersede_is_manual_and_audited(session, production_manager, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, Decimal("50.000"))
    batch = mark_batch_superseded(
        session, batch_id=batch_id, actor_user_id=production_manager.user_id, reason="Downgrade via Sortasi"
    )
    assert batch.status == BatchStatus.SUPERSEDED
