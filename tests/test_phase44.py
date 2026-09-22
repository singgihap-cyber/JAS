"""Fase 44 -- koreksi tanggal & pembatalan (soft-cancel) event historis
(Production Manager, alasan wajib, hanya event tanpa turunan)."""
import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventStatus, QCStage
from traceability_engine.exceptions import EventDateOrderError, InvalidEventStructureError, UnauthorizedDispositionError
from traceability_engine.models import Batch, EventCancellation, EventDateCorrection, ProcessEvent
from traceability_engine.services.event_correction import (
    cancel_event, correct_event_date, list_correctable_events, list_event_correction_history,
)
from traceability_engine.services.qc_md import QCTestInput, record_qc_test
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.services.stock import reconcile_batch

Q = Decimal


def _received_batch(session, staff, supplier, event_date=dt.date(2026, 9, 1), qty=Q("100")):
    ev = record_receiving(session, ReceivingInput(
        event_date=event_date, pic_user_id=staff.user_id, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_HIJAU, net_quantity=qty, jenis_code="02", grade_code="00"))
    batch = session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()
    return ev, batch


def _qc_event(session, staff, batch, event_date):
    return record_qc_test(session, QCTestInput(
        event_date=event_date, pic_user_id=staff.user_id, batch_id=batch.batch_id,
        stage=QCStage.RM, ka_1=Q("35.0")))


# ------------------------------------------------------------- correct_event_date

def test_correct_event_date_success(session, staff_user, production_manager, supplier):
    _, batch = _received_batch(session, staff_user, supplier, event_date=dt.date(2026, 9, 1))
    qc = _qc_event(session, staff_user, batch, dt.date(2026, 9, 10))

    entry = correct_event_date(
        session, event_id=qc.event_id, new_event_date=dt.date(2026, 9, 12),
        actor_user_id=production_manager.user_id, reason="Tanggal QC salah ketik")

    refreshed = session.get(ProcessEvent, qc.event_id)
    assert refreshed.event_date == dt.date(2026, 9, 12)
    assert entry.old_event_date == dt.date(2026, 9, 10)
    assert entry.new_event_date == dt.date(2026, 9, 12)
    assert session.query(EventDateCorrection).filter_by(event_id=qc.event_id).count() == 1


def test_correct_event_date_rejects_order_violation(session, staff_user, production_manager, supplier):
    """Padanan kasus nyata MD 15/6 pada batch 030218-260618-00 (Fase 23/28/37):
    tidak boleh mengoreksi tanggal QC jadi lebih awal dari Receiving."""
    _, batch = _received_batch(session, staff_user, supplier, event_date=dt.date(2026, 9, 10))
    qc = _qc_event(session, staff_user, batch, dt.date(2026, 9, 12))
    with pytest.raises(EventDateOrderError):
        correct_event_date(
            session, event_id=qc.event_id, new_event_date=dt.date(2026, 9, 5),
            actor_user_id=production_manager.user_id, reason="coba mundurkan")
    assert session.get(ProcessEvent, qc.event_id).event_date == dt.date(2026, 9, 12)


def test_correct_event_date_guards(session, staff_user, production_manager, supplier):
    _, batch = _received_batch(session, staff_user, supplier)
    qc = _qc_event(session, staff_user, batch, dt.date(2026, 9, 10))
    pm = production_manager.user_id

    with pytest.raises(UnauthorizedDispositionError):
        correct_event_date(session, event_id=qc.event_id, new_event_date=dt.date(2026, 9, 11),
                           actor_user_id=staff_user.user_id, reason="x")
    with pytest.raises(InvalidEventStructureError, match="Alasan"):
        correct_event_date(session, event_id=qc.event_id, new_event_date=dt.date(2026, 9, 11),
                           actor_user_id=pm, reason="  ")
    with pytest.raises(InvalidEventStructureError, match="tidak ditemukan"):
        correct_event_date(session, event_id=999999, new_event_date=dt.date(2026, 9, 11),
                           actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="sama"):
        correct_event_date(session, event_id=qc.event_id, new_event_date=dt.date(2026, 9, 10),
                           actor_user_id=pm, reason="x")

    cancel_event(session, event_id=qc.event_id, actor_user_id=pm, reason="dibatalkan dulu")
    with pytest.raises(InvalidEventStructureError, match="VOID"):
        correct_event_date(session, event_id=qc.event_id, new_event_date=dt.date(2026, 9, 11),
                           actor_user_id=pm, reason="x")


def test_correct_event_date_blocked_by_downstream(session, staff_user, production_manager, supplier):
    receiving_ev, batch = _received_batch(session, staff_user, supplier, event_date=dt.date(2026, 9, 1))
    record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))
    with pytest.raises(InvalidEventStructureError, match="turunan"):
        correct_event_date(session, event_id=receiving_ev.event_id, new_event_date=dt.date(2026, 9, 2),
                           actor_user_id=production_manager.user_id, reason="x")


# ------------------------------------------------------------------ cancel_event

def test_cancel_event_self_loop_reverses_stock_and_marks_void(session, staff_user, production_manager, supplier):
    _, batch = _received_batch(session, staff_user, supplier, qty=Q("50"))
    qc = _qc_event(session, staff_user, batch, dt.date(2026, 9, 10))
    before_qty = session.get(Batch, batch.batch_id).current_quantity

    entry = cancel_event(session, event_id=qc.event_id, actor_user_id=production_manager.user_id,
                         reason="QC ganda, salah input")

    refreshed_event = session.get(ProcessEvent, qc.event_id)
    assert refreshed_event.status == EventStatus.VOID
    refreshed_batch = session.get(Batch, batch.batch_id)
    assert refreshed_batch.current_quantity == before_qty  # self-loop bersih -> tak berubah
    assert reconcile_batch(session, batch.batch_id).matches
    assert isinstance(entry, EventCancellation) and entry.event_type == "QC_TEST"
    assert session.query(EventCancellation).filter_by(event_id=qc.event_id).count() == 1


def test_cancel_event_receiving_reverses_to_zero_and_consumed(session, staff_user, production_manager, supplier):
    receiving_ev, batch = _received_batch(session, staff_user, supplier, qty=Q("30"))
    assert session.get(Batch, batch.batch_id).status == BatchStatus.ACTIVE

    cancel_event(session, event_id=receiving_ev.event_id, actor_user_id=production_manager.user_id,
                reason="Receiving dobel, salah catat")

    refreshed = session.get(Batch, batch.batch_id)
    assert refreshed.current_quantity == Q("0")
    assert refreshed.status == BatchStatus.CONSUMED
    assert reconcile_batch(session, batch.batch_id).matches
    assert session.get(ProcessEvent, receiving_ev.event_id).status == EventStatus.VOID


def test_cancel_event_blocked_by_downstream(session, staff_user, production_manager, supplier):
    receiving_ev, batch = _received_batch(session, staff_user, supplier)
    record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))
    with pytest.raises(InvalidEventStructureError, match="turunan"):
        cancel_event(session, event_id=receiving_ev.event_id, actor_user_id=production_manager.user_id, reason="x")
    assert session.get(ProcessEvent, receiving_ev.event_id).status == EventStatus.COMPLETED


def test_cancel_event_guards(session, staff_user, production_manager, supplier):
    _, batch = _received_batch(session, staff_user, supplier)
    qc = _qc_event(session, staff_user, batch, dt.date(2026, 9, 10))
    pm = production_manager.user_id

    with pytest.raises(UnauthorizedDispositionError):
        cancel_event(session, event_id=qc.event_id, actor_user_id=staff_user.user_id, reason="x")
    with pytest.raises(InvalidEventStructureError, match="Alasan"):
        cancel_event(session, event_id=qc.event_id, actor_user_id=pm, reason=" ")

    cancel_event(session, event_id=qc.event_id, actor_user_id=pm, reason="dibatalkan")
    with pytest.raises(InvalidEventStructureError, match="sudah dibatalkan"):
        cancel_event(session, event_id=qc.event_id, actor_user_id=pm, reason="dibatalkan lagi")


# --------------------------------------------------------------------- listing

def test_list_correctable_events_excludes_events_with_downstream(session, staff_user, production_manager, supplier):
    receiving_ev, batch = _received_batch(session, staff_user, supplier)
    sort_ev = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))

    ids = {r.event_id for r in list_correctable_events(session)}
    assert receiving_ev.event_id not in ids  # sudah punya turunan (Sortasi)
    assert sort_ev.event_id in ids  # belum ada turunan setelahnya

    filtered = {r.event_id for r in list_correctable_events(session, batch_id=batch.batch_id)}
    assert filtered == {sort_ev.event_id}  # RECEIVING difilter keluar oleh downstream-check


def test_list_event_correction_history_combines_both_kinds(session, staff_user, production_manager, supplier):
    _, batch = _received_batch(session, staff_user, supplier)
    qc = _qc_event(session, staff_user, batch, dt.date(2026, 9, 10))
    pm = production_manager.user_id

    correct_event_date(session, event_id=qc.event_id, new_event_date=dt.date(2026, 9, 11),
                       actor_user_id=pm, reason="koreksi tanggal")
    cancel_event(session, event_id=qc.event_id, actor_user_id=pm, reason="lalu dibatalkan")

    history = list_event_correction_history(session, event_id=qc.event_id)
    assert [h.kind for h in history] == ["DATE_CORRECTION", "CANCELLATION"]
    assert history[0].detail == "2026-09-10 -> 2026-09-11"
    assert "dibatalkan" in history[1].reason
