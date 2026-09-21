"""Fase 42 -- tandai-tinjau temuan audit AA + koreksi nomor batch (Production Manager)."""
import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchType
from traceability_engine.exceptions import (
    BatchNumberConflictError, InvalidEventStructureError, UnauthorizedDispositionError,
)
from traceability_engine.models import AuditLog, Batch, BatchNumberCorrection
from traceability_engine.services.aa_chain import audit_aa_chain
from traceability_engine.services.aa_review import (
    correct_batch_number, list_number_history, review_aa_finding,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation


def _finding(session, staff, supplier):
    ev = record_receiving(session, ReceivingInput(
        event_date=dt.date(2026, 9, 1), pic_user_id=staff.user_id, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_HIJAU, net_quantity=Decimal("100"), jenis_code="02", grade_code="00"))
    src = session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()
    record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 21), pic_user_id=staff.user_id, batch_id=src.batch_id,
        eg_qty=Decimal("60"), gourmet_qty=Decimal("30"), process_code="00",
        eg_batch_number="0102024-260918-00", gourmet_batch_number="0201024-260918-00"))
    [f] = audit_aa_chain(session, hijau_only=True)
    return f


def test_new_finding_defaults_to_baru(session, staff_user, supplier):
    f = _finding(session, staff_user, supplier)
    assert f.review_status == "BARU" and f.reviewed_by is None
    assert audit_aa_chain(session, review_status="BARU") == [f]
    assert audit_aa_chain(session, review_status="DITINJAU") == []


def test_review_only_manager_with_note(session, staff_user, production_manager, supplier):
    f = _finding(session, staff_user, supplier)
    key = dict(event_id=f.event_id, source_batch_id=f.source_batch_id, result_batch_id=f.result_batch_id)
    with pytest.raises(UnauthorizedDispositionError):
        review_aa_finding(session, **key, status="DITINJAU", actor_user_id=staff_user.user_id, note="ok")
    with pytest.raises(InvalidEventStructureError, match="Catatan"):
        review_aa_finding(session, **key, status="DITINJAU", actor_user_id=production_manager.user_id, note=" ")
    with pytest.raises(InvalidEventStructureError, match="tidak valid"):
        review_aa_finding(session, **key, status="DIKOREKSI", actor_user_id=production_manager.user_id, note="x")
    review_aa_finding(session, **key, status="DITINJAU", actor_user_id=production_manager.user_id, note="Wajar, campuran")
    [g] = audit_aa_chain(session, hijau_only=True)
    assert g.review_status == "DITINJAU" and g.review_note == "Wajar, campuran"
    assert g.reviewed_by == production_manager.user_id and g.reviewed_at is not None
    review_aa_finding(session, **key, status="DIABAIKAN", actor_user_id=production_manager.user_id, note="Diabaikan")
    assert audit_aa_chain(session, review_status="DIABAIKAN")[0].review_note == "Diabaikan"  # upsert, bukan baris ganda
    with pytest.raises(InvalidEventStructureError, match="tidak ada"):
        review_aa_finding(session, **{**key, "event_id": 9999}, status="DITINJAU",
                          actor_user_id=production_manager.user_id, note="x")


def test_correct_number_updates_components_history_and_marks_dikoreksi(session, staff_user, production_manager, supplier):
    f = _finding(session, staff_user, supplier)
    entry = correct_batch_number(
        session, batch_id=f.result_batch_id, new_batch_number="0202024-260918-00",
        actor_user_id=production_manager.user_id, reason="Salah ketik AA")
    b = session.get(Batch, f.result_batch_id)
    assert b.batch_number == "0202024-260918-00" and b.jenis_code == "02"
    assert entry.old_batch_number == "0102024-260918-00"
    assert [h.reason for h in list_number_history(session, batch_id=b.batch_id)] == ["Salah ketik AA"]
    assert audit_aa_chain(session, hijau_only=True) == []  # AA kini konsisten -> temuan hilang
    assert session.query(AuditLog).filter(AuditLog.after_value.like("batch_number 0202024-260918-00%")).count() == 1


def test_correct_number_marks_remaining_finding_dikoreksi(session, staff_user, production_manager, supplier):
    f = _finding(session, staff_user, supplier)
    correct_batch_number(session, batch_id=f.result_batch_id, new_batch_number="0102024-260919-00",
                         actor_user_id=production_manager.user_id, reason="Tanggal salah")
    [g] = audit_aa_chain(session, hijau_only=True)  # AA masih 01 -> temuan tetap ada
    assert g.review_status == "DIKOREKSI" and "Tanggal salah" in g.review_note


def test_correct_number_guards(session, staff_user, production_manager, supplier):
    f = _finding(session, staff_user, supplier)
    pm = production_manager.user_id
    other = session.query(Batch).filter(Batch.batch_number == "0201024-260918-00").one()
    with pytest.raises(UnauthorizedDispositionError):
        correct_batch_number(session, batch_id=f.result_batch_id, new_batch_number="0202024-260918-00",
                             actor_user_id=staff_user.user_id, reason="x")
    with pytest.raises(InvalidEventStructureError, match="Alasan"):
        correct_batch_number(session, batch_id=f.result_batch_id, new_batch_number="0202024-260918-00",
                             actor_user_id=pm, reason=" ")
    with pytest.raises(InvalidEventStructureError, match="tidak valid"):
        correct_batch_number(session, batch_id=f.result_batch_id, new_batch_number="ngawur", actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="sama"):
        correct_batch_number(session, batch_id=f.result_batch_id, new_batch_number="0102024-260918-00",
                             actor_user_id=pm, reason="x")
    with pytest.raises(BatchNumberConflictError):
        correct_batch_number(session, batch_id=f.result_batch_id, new_batch_number=other.batch_number,
                             actor_user_id=pm, reason="x")
    assert session.query(BatchNumberCorrection).count() == 0
    assert session.get(Batch, f.result_batch_id).batch_number == "0102024-260918-00"
