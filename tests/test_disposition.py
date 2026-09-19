"""Fase 26 -- disposisi batch REJECTED: dikembalikan ke supplier
(services/disposition.py). Aturan terkonfirmasi user 2026-09-19: nasib =
kembali ke supplier; pemutus = PRODUCTION_MANAGER; penegakan = laporan saja.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.exceptions import (
    InvalidEventStructureError, UnauthorizedDispositionError,
)
from traceability_engine.models import AuditLog, Batch, ProcessEvent, StockTransaction
from traceability_engine.services.adjustment import mark_batch_rejected
from traceability_engine.services.disposition import audit_disposition, return_to_supplier
from traceability_engine.services.events import InputSpec, OutputSpec, record_process_event
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.stock import reconcile_batch

D18 = dt.date(2026, 6, 18)


def _batch(session, uid, supplier, net=D("10")):
    ev = record_receiving(session, ReceivingInput(
        event_date=D18, pic_user_id=uid, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_KERING, net_quantity=net))
    return session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


def _reject(session, uid, b, reason="MD: logam"):
    return mark_batch_rejected(session, batch_id=b.batch_id, actor_user_id=uid, reason=reason)


def test_full_return_zeroes_stock_and_writes_ledger_and_audit(session, staff_user, production_manager, supplier):
    b = _batch(session, staff_user.user_id, supplier)
    _reject(session, staff_user.user_id, b)
    ev = return_to_supplier(session, batch_id=b.batch_id, actor_user_id=production_manager.user_id,
                            reason="Ditolak MD1, kembali ke supplier", event_date=dt.date(2026, 6, 20))
    assert ev.event_type == EventType.SUPPLIER_RETURN
    assert b.current_quantity == D("0") and b.status == BatchStatus.REJECTED
    tx = session.query(StockTransaction).filter_by(event_id=ev.event_id).one()
    assert tx.direction.value == "OUT" and tx.quantity == D("10") and tx.balance_after == D("0")
    reconcile_batch(session, b.batch_id)  # ledger konsisten
    assert "WARDOYO" in ev.notes and "kembali ke supplier" in ev.notes
    log = session.execute(select(AuditLog).where(AuditLog.after_value.like("RETURNED_TO_SUPPLIER%"))).scalar_one()
    assert log.actor_user_id == production_manager.user_id and log.before_value == "REJECTED qty=10.000"


def test_partial_return_then_rest(session, staff_user, production_manager, supplier):
    b = _batch(session, staff_user.user_id, supplier)
    _reject(session, staff_user.user_id, b)
    pm = production_manager.user_id
    return_to_supplier(session, batch_id=b.batch_id, actor_user_id=pm, reason="sebagian",
                       event_date=D18, quantity=D("4"))
    [row] = audit_disposition(session)
    assert row.disposition == "PARTIALLY_RETURNED" and row.returned_quantity == D("4")
    assert row.quantity_on_hand == D("6")
    return_to_supplier(session, batch_id=b.batch_id, actor_user_id=pm, reason="sisa", event_date=D18)
    [row] = audit_disposition(session)
    assert row.disposition == "RETURNED" and row.returned_quantity == D("10") and len(row.return_event_ids) == 2


def test_only_production_manager_may_decide(session, staff_user, supplier):
    b = _batch(session, staff_user.user_id, supplier)
    _reject(session, staff_user.user_id, b)
    with pytest.raises(UnauthorizedDispositionError):
        return_to_supplier(session, batch_id=b.batch_id, actor_user_id=staff_user.user_id,
                           reason="x", event_date=D18)
    with pytest.raises(UnauthorizedDispositionError):
        return_to_supplier(session, batch_id=b.batch_id, actor_user_id=9999, reason="x", event_date=D18)
    assert b.current_quantity == D("10")


def test_requires_rejected_status_reason_and_stock(session, staff_user, production_manager, supplier):
    pm = production_manager.user_id
    b = _batch(session, staff_user.user_id, supplier)
    with pytest.raises(InvalidEventStructureError, match="REJECTED"):
        return_to_supplier(session, batch_id=b.batch_id, actor_user_id=pm, reason="x", event_date=D18)
    _reject(session, staff_user.user_id, b)
    with pytest.raises(InvalidEventStructureError, match="reason"):
        return_to_supplier(session, batch_id=b.batch_id, actor_user_id=pm, reason="  ", event_date=D18)
    with pytest.raises(InvalidEventStructureError, match="does not exist"):
        return_to_supplier(session, batch_id=999, actor_user_id=pm, reason="x", event_date=D18)
    from traceability_engine.exceptions import InsufficientStockError
    with pytest.raises(InsufficientStockError):
        return_to_supplier(session, batch_id=b.batch_id, actor_user_id=pm, reason="x",
                           event_date=D18, quantity=D("11"))
    return_to_supplier(session, batch_id=b.batch_id, actor_user_id=pm, reason="x", event_date=D18)
    with pytest.raises(InvalidEventStructureError, match="positive"):
        return_to_supplier(session, batch_id=b.batch_id, actor_user_id=pm, reason="x", event_date=D18)


def test_audit_reports_pending_and_ignores_non_rejected(session, staff_user, supplier):
    ok = _batch(session, staff_user.user_id, supplier)
    bad = _batch(session, staff_user.user_id, supplier)
    _reject(session, staff_user.user_id, bad, reason="AW tinggi")
    [row] = audit_disposition(session)
    assert row.batch_id == bad.batch_id and row.disposition == "PENDING_RETURN"
    assert row.reject_reason == "AW tinggi" and row.supplier_name == "WARDOYO"
    assert "belum dikembalikan" in row.message()
    assert audit_disposition(session, batch_id=ok.batch_id) == []


def test_use_after_rejection_is_warned_not_blocked(session, staff_user, supplier):
    b = _batch(session, staff_user.user_id, supplier)
    _reject(session, staff_user.user_id, b)
    # Tidak diblokir (penegakan = laporan saja):
    ev = record_process_event(
        session, event_type=EventType.SUNDRYING, event_date=D18, pic_user_id=staff_user.user_id,
        inputs=[InputSpec(b.batch_id, D("1"))], outputs=[OutputSpec(D("1"), batch_id=b.batch_id)])
    # created_at server_default resolusi detik -> atur eksplisit
    log = session.execute(select(AuditLog).where(AuditLog.entity_id == b.batch_id)).scalars().all()[-1]
    log.timestamp = dt.datetime(2026, 6, 18, 8, 0, 0)
    ev.created_at = dt.datetime(2026, 6, 18, 9, 0, 0)
    session.flush()
    [row] = audit_disposition(session)
    assert row.used_after_rejection_event_ids == [ev.event_id] and "PERINGATAN" in row.message()
    # Sebelum penolakan -> tidak ditandai
    ev.created_at = dt.datetime(2026, 6, 18, 7, 0, 0)
    session.flush()
    assert audit_disposition(session)[0].used_after_rejection_event_ids == []


def test_qc_md_events_after_rejection_are_not_usage(session, staff_user, supplier):
    from traceability_engine.enums import QCStage
    from traceability_engine.services.qc_md import MetalDetectionInput, record_metal_detection
    b = _batch(session, staff_user.user_id, supplier)
    _reject(session, staff_user.user_id, b)
    ev = record_metal_detection(session, MetalDetectionInput(
        event_date=D18, pic_user_id=staff_user.user_id, batch_id=b.batch_id, stage=QCStage.RM))
    log = session.execute(select(AuditLog).where(AuditLog.entity_id == b.batch_id)).scalars().all()[-1]
    log.timestamp = dt.datetime(2026, 6, 18, 8, 0, 0)
    ev.created_at = dt.datetime(2026, 6, 18, 9, 0, 0)
    session.flush()
    assert audit_disposition(session)[0].used_after_rejection_event_ids == []


def test_void_event_is_ignored(session, staff_user, supplier):
    from traceability_engine.enums import EventStatus
    b = _batch(session, staff_user.user_id, supplier)
    _reject(session, staff_user.user_id, b)
    ev = record_process_event(
        session, event_type=EventType.SUNDRYING, event_date=D18, pic_user_id=staff_user.user_id,
        inputs=[InputSpec(b.batch_id, D("1"))], outputs=[OutputSpec(D("1"), batch_id=b.batch_id)])
    session.execute(select(AuditLog)).scalars().all()[-1].timestamp = dt.datetime(2026, 6, 18, 8, 0, 0)
    ev.created_at = dt.datetime(2026, 6, 18, 9, 0, 0)
    ev.status = EventStatus.VOID
    session.flush()
    assert audit_disposition(session)[0].used_after_rejection_event_ids == []
