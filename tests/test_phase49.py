"""Fase 49 -- koreksi susut/loss event historis + audit keseimbangan.

Keputusan user (2026-09-23, AskUserQuestion "Fase 49"):
1. Susut ikut OTOMATIS saat kuantitas link dikoreksi (Sundrying 100 -> 80
   susut 20; output dikoreksi 82 -> susut 18).
2. Koreksi susut/loss langsung = hanya pindah susut<->loss, total tetap.
3. Pemindahan susut<->loss boleh walau event sudah punya turunan.
4. Laporan audit event tidak seimbang (tidak memblokir).
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from traceability_engine.enums import BatchType, EventStatus, LinkRole
from traceability_engine.exceptions import InvalidEventStructureError, UnauthorizedDispositionError
from traceability_engine.models import (
    AuditLog, Batch, EventBatchLink, EventShrinkageCorrection, ProcessEvent,
)
from traceability_engine.services.event_correction import (
    cancel_event, correct_event_quantity, list_event_correction_history, list_unbalanced_events,
    shrinkage_change_for, transfer_shrinkage_loss,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.services.steam_dry import SundryingInput, record_sundrying
from traceability_engine.services.stock import reconcile_batch

Q = Decimal


def _received(session, staff, supplier, qty=Q("100"), day=1):
    ev = record_receiving(session, ReceivingInput(
        event_date=dt.date(2026, 9, day), pic_user_id=staff.user_id, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_KERING, net_quantity=qty, jenis_code="02", grade_code="00"))
    return ev, session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


def _sundried(session, staff, supplier):
    """Contoh user: Sundrying masuk 100, keluar 80, susut 20."""
    _, batch = _received(session, staff, supplier)
    sd = record_sundrying(session, SundryingInput(
        event_date=dt.date(2026, 9, 3), pic_user_id=staff.user_id, batch_id=batch.batch_id,
        starting_quantity=Q("100"), final_quantity=Q("80")))
    assert sd.shrinkage_qty == Q("20")
    return sd, batch


def _link(session, event_id, role):
    return session.execute(select(EventBatchLink).where(
        EventBatchLink.event_id == event_id, EventBatchLink.role == role)).scalars().first()


# ------------------------------------------------ 1. susut ikut otomatis

def test_output_correction_adjusts_shrinkage_user_example(session, staff_user, production_manager, supplier):
    sd, batch = _sundried(session, staff_user, supplier)
    out = _link(session, sd.event_id, LinkRole.OUTPUT)
    entry = correct_event_quantity(
        session, event_id=sd.event_id, link_id=out.link_id, new_quantity=Q("82"),
        actor_user_id=production_manager.user_id, reason="Hasil jemur salah timbang")

    ev = session.get(ProcessEvent, sd.event_id)
    assert ev.shrinkage_qty == Q("18")  # 100 = 82 + 18
    assert ev.loss_qty == Q("0")
    assert session.get(Batch, batch.batch_id).current_quantity == Q("82")
    assert reconcile_batch(session, batch.batch_id).matches
    change = shrinkage_change_for(session, entry.correction_id)
    assert change.source == "AUTO_QUANTITY"
    assert (change.old_shrinkage_qty, change.new_shrinkage_qty) == (Q("20"), Q("18"))
    assert list_unbalanced_events(session) == []


def test_input_correction_adjusts_shrinkage(session, staff_user, production_manager, supplier):
    _, batch = _received(session, staff_user, supplier, qty=Q("100"))
    sort_ev = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        initial_qty=Q("50"), eg_qty=Q("45")))
    assert sort_ev.shrinkage_qty == Q("5")
    inp = _link(session, sort_ev.event_id, LinkRole.INPUT)
    correct_event_quantity(
        session, event_id=sort_ev.event_id, link_id=inp.link_id, new_quantity=Q("48"),
        actor_user_id=production_manager.user_id, reason="Qty awal sortasi salah")
    assert session.get(ProcessEvent, sort_ev.event_id).shrinkage_qty == Q("3")  # 48 = 45 + 3
    assert list_unbalanced_events(session) == []


def test_receiving_correction_leaves_shrinkage_alone(session, staff_user, production_manager, supplier):
    ev, batch = _received(session, staff_user, supplier, qty=Q("50"))
    out = _link(session, ev.event_id, LinkRole.OUTPUT)
    entry = correct_event_quantity(
        session, event_id=ev.event_id, link_id=out.link_id, new_quantity=Q("55"),
        actor_user_id=production_manager.user_id, reason="x")
    assert session.get(ProcessEvent, ev.event_id).shrinkage_qty == Q("0")
    assert shrinkage_change_for(session, entry.correction_id) is None


def test_negative_shrinkage_is_allowed_but_recorded(session, staff_user, production_manager, supplier):
    sd, _ = _sundried(session, staff_user, supplier)
    out = _link(session, sd.event_id, LinkRole.OUTPUT)
    correct_event_quantity(
        session, event_id=sd.event_id, link_id=out.link_id, new_quantity=Q("105"),
        actor_user_id=production_manager.user_id, reason="coba")
    assert session.get(ProcessEvent, sd.event_id).shrinkage_qty == Q("-5")
    assert list_unbalanced_events(session) == []  # tetap seimbang: 100 = 105 - 5


def test_history_lists_auto_shrinkage_after_quantity(session, staff_user, production_manager, supplier):
    sd, _ = _sundried(session, staff_user, supplier)
    out = _link(session, sd.event_id, LinkRole.OUTPUT)
    correct_event_quantity(
        session, event_id=sd.event_id, link_id=out.link_id, new_quantity=Q("82"),
        actor_user_id=production_manager.user_id, reason="Hasil jemur salah timbang")
    kinds = [h.kind for h in list_event_correction_history(session, event_id=sd.event_id)]
    assert kinds == ["QUANTITY_CORRECTION", "SHRINKAGE_CORRECTION"]


# ------------------------------------------------ 2+3. pindah susut <-> loss

def test_transfer_shrinkage_to_loss(session, staff_user, production_manager, supplier):
    sd, batch = _sundried(session, staff_user, supplier)
    before_stock = session.get(Batch, batch.batch_id).current_quantity
    entry = transfer_shrinkage_loss(
        session, event_id=sd.event_id, new_shrinkage_qty=Q("18"), new_loss_qty=Q("2"),
        actor_user_id=production_manager.user_id, reason="2 kg tercecer, bukan penyusutan")
    ev = session.get(ProcessEvent, sd.event_id)
    assert (ev.shrinkage_qty, ev.loss_qty) == (Q("18"), Q("2"))
    assert entry.source == "TRANSFER" and entry.quantity_correction_id is None
    assert session.get(Batch, batch.batch_id).current_quantity == before_stock
    assert list_unbalanced_events(session) == []
    assert session.query(AuditLog).filter_by(entity_type="ProcessEvent", entity_id=sd.event_id).count() == 1


def test_transfer_guards(session, staff_user, production_manager, supplier):
    sd, _ = _sundried(session, staff_user, supplier)
    pm = production_manager.user_id
    kw = dict(event_id=sd.event_id, new_shrinkage_qty=Q("18"), new_loss_qty=Q("2"), reason="r")
    with pytest.raises(UnauthorizedDispositionError):
        transfer_shrinkage_loss(session, actor_user_id=staff_user.user_id, **kw)
    with pytest.raises(InvalidEventStructureError, match="Alasan"):
        transfer_shrinkage_loss(session, actor_user_id=pm, **{**kw, "reason": "  "})
    with pytest.raises(InvalidEventStructureError, match="Total susut"):
        transfer_shrinkage_loss(session, actor_user_id=pm, **{**kw, "new_loss_qty": Q("5")})
    with pytest.raises(InvalidEventStructureError, match="negatif"):
        transfer_shrinkage_loss(session, actor_user_id=pm, **{**kw, "new_shrinkage_qty": Q("21"),
                                                            "new_loss_qty": Q("-1")})
    with pytest.raises(InvalidEventStructureError, match="sama"):
        transfer_shrinkage_loss(session, actor_user_id=pm, **{**kw, "new_shrinkage_qty": Q("20"),
                                                            "new_loss_qty": Q("0")})
    with pytest.raises(InvalidEventStructureError, match="tidak ditemukan"):
        transfer_shrinkage_loss(session, actor_user_id=pm, **{**kw, "event_id": 9999})


def test_transfer_rejects_receiving_and_void(session, staff_user, production_manager, supplier):
    rec, _ = _received(session, staff_user, supplier, day=2)
    pm = production_manager.user_id
    with pytest.raises(InvalidEventStructureError, match="tidak punya susut"):
        transfer_shrinkage_loss(session, event_id=rec.event_id, new_shrinkage_qty=Q("0"),
                                new_loss_qty=Q("0"), actor_user_id=pm, reason="r")
    sd, _ = _sundried(session, staff_user, supplier)
    cancel_event(session, event_id=sd.event_id, actor_user_id=pm, reason="salah input")
    with pytest.raises(InvalidEventStructureError, match="VOID"):
        transfer_shrinkage_loss(session, event_id=sd.event_id, new_shrinkage_qty=Q("18"),
                                new_loss_qty=Q("2"), actor_user_id=pm, reason="r")


def test_transfer_allowed_even_with_downstream(session, staff_user, production_manager, supplier):
    sd, batch = _sundried(session, staff_user, supplier)
    record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 6), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("80")))
    out = _link(session, sd.event_id, LinkRole.OUTPUT)
    with pytest.raises(InvalidEventStructureError, match="turunan"):
        correct_event_quantity(session, event_id=sd.event_id, link_id=out.link_id, new_quantity=Q("82"),
                               actor_user_id=production_manager.user_id, reason="x")
    transfer_shrinkage_loss(session, event_id=sd.event_id, new_shrinkage_qty=Q("15"), new_loss_qty=Q("5"),
                            actor_user_id=production_manager.user_id, reason="loss nyata")
    assert session.get(ProcessEvent, sd.event_id).loss_qty == Q("5")


# ------------------------------------------------ 4. audit keseimbangan

def test_unbalanced_report_finds_legacy_imbalance(session, staff_user, production_manager, supplier):
    sd, batch = _sundried(session, staff_user, supplier)
    # Simulasi data lama (koreksi Fase 45 sebelum Fase 49): output diubah tanpa susut ikut.
    out = _link(session, sd.event_id, LinkRole.OUTPUT)
    out.quantity = Q("82")
    session.flush()

    rows = list_unbalanced_events(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.event_id == sd.event_id and row.event_type == "SUNDRYING"
    assert (row.sum_input, row.sum_output, row.shrinkage_qty, row.difference) == (Q("100"), Q("82"), Q("20"), Q("-2"))
    assert row.has_downstream is False
    assert list_unbalanced_events(session, batch_id=batch.batch_id)[0].event_id == sd.event_id
    assert list_unbalanced_events(session, batch_id=9999) == []

    # Koreksi kuantitas berikutnya menggeser susut sebesar selisih -> ketidakseimbangan lama tetap.
    correct_event_quantity(session, event_id=sd.event_id, link_id=out.link_id, new_quantity=Q("81"),
                           actor_user_id=production_manager.user_id, reason="timbang ulang")
    assert list_unbalanced_events(session)[0].difference == Q("-2")

    # Event VOID tidak dilaporkan.
    cancel_event(session, event_id=sd.event_id, actor_user_id=production_manager.user_id, reason="batal")
    assert session.get(ProcessEvent, sd.event_id).status == EventStatus.VOID
    assert list_unbalanced_events(session) == []
