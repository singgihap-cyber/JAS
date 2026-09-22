"""Fase 45 -- koreksi kuantitas event historis + rantai blocking ("cascade
manual bertahap"). Backlog turunan Fase 44 (PROJECT_STATUS.md).

Keputusan user (2026-09-22, AskUserQuestion "Fase 45"):
1. Koreksi kuantitas berlaku generik untuk SEMUA tipe event, hanya
   Production Manager, alasan wajib, hanya event tanpa turunan (sama
   syarat dengan Fase 44); StockTransaction & Batch.current_quantity
   disesuaikan OTOMATIS lewat baris kompensasi.
2. Event yang sudah punya turunan TIDAK dibuka cascade otomatis --
   `get_blocking_chain()` hanya menampilkan rantai transitif yang harus
   dibatalkan satu-satu (terbaru dulu) lewat `cancel_event()` yang sudah
   ada di Fase 44.
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from traceability_engine.enums import BatchStatus, BatchType, LinkRole, QCStage
from traceability_engine.exceptions import (
    InsufficientStockError, InvalidEventStructureError, UnauthorizedDispositionError,
)
from traceability_engine.models import Batch, EventBatchLink, EventQuantityCorrection, ProcessEvent
from traceability_engine.services.event_correction import (
    cancel_event, correct_event_date, correct_event_quantity, get_blocking_chain,
    list_event_correction_history, list_event_links,
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


def _output_link(session, event_id, batch_id):
    return session.execute(
        select(EventBatchLink).where(
            EventBatchLink.event_id == event_id, EventBatchLink.batch_id == batch_id,
            EventBatchLink.role == LinkRole.OUTPUT,
        )
    ).scalar_one()


def _input_link(session, event_id, batch_id):
    return session.execute(
        select(EventBatchLink).where(
            EventBatchLink.event_id == event_id, EventBatchLink.batch_id == batch_id,
            EventBatchLink.role == LinkRole.INPUT,
        )
    ).scalar_one()


# ------------------------------------------------------------ correct_event_quantity

def test_correct_quantity_output_increase_and_decrease(session, staff_user, production_manager, supplier):
    ev, batch = _received_batch(session, staff_user, supplier, qty=Q("50"))
    pm = production_manager.user_id
    link = _output_link(session, ev.event_id, batch.batch_id)

    entry = correct_event_quantity(
        session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("70"),
        actor_user_id=pm, reason="Timbangan awal salah baca")
    assert entry.old_quantity == Q("50") and entry.new_quantity == Q("70")
    assert session.get(Batch, batch.batch_id).current_quantity == Q("70")
    assert reconcile_batch(session, batch.batch_id).matches
    assert session.query(EventQuantityCorrection).filter_by(event_id=ev.event_id).count() == 1

    correct_event_quantity(
        session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("40"),
        actor_user_id=pm, reason="Dikoreksi lagi setelah ditimbang ulang")
    assert session.get(Batch, batch.batch_id).current_quantity == Q("40")
    assert reconcile_batch(session, batch.batch_id).matches
    assert session.query(EventQuantityCorrection).filter_by(event_id=ev.event_id).count() == 2


def test_correct_quantity_input_decrease_returns_stock_and_reactivates(
    session, staff_user, production_manager, supplier
):
    _, batch = _received_batch(session, staff_user, supplier, qty=Q("100"))
    sort_ev = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))
    assert session.get(Batch, batch.batch_id).status == BatchStatus.CONSUMED

    link = _input_link(session, sort_ev.event_id, batch.batch_id)
    correct_event_quantity(
        session, event_id=sort_ev.event_id, link_id=link.link_id, new_quantity=Q("60"),
        actor_user_id=production_manager.user_id, reason="Initial qty sortasi salah catat")

    refreshed = session.get(Batch, batch.batch_id)
    assert refreshed.current_quantity == Q("40")  # 40 dikembalikan
    assert refreshed.status == BatchStatus.ACTIVE  # tidak lagi 0 -> ACTIVE lagi
    assert reconcile_batch(session, batch.batch_id).matches


def test_correct_quantity_input_increase_rejects_insufficient_stock(
    session, staff_user, production_manager, supplier
):
    _, batch = _received_batch(session, staff_user, supplier, qty=Q("100"))
    sort_ev = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))
    link = _input_link(session, sort_ev.event_id, batch.batch_id)

    with pytest.raises(InsufficientStockError):
        correct_event_quantity(
            session, event_id=sort_ev.event_id, link_id=link.link_id, new_quantity=Q("150"),
            actor_user_id=production_manager.user_id, reason="coba naikkan melebihi stok")
    assert session.get(Batch, batch.batch_id).current_quantity == Q("0")  # tidak berubah


def test_correct_quantity_guards(session, staff_user, production_manager, supplier):
    ev, batch = _received_batch(session, staff_user, supplier, qty=Q("50"))
    pm = production_manager.user_id
    link = _output_link(session, ev.event_id, batch.batch_id)

    with pytest.raises(UnauthorizedDispositionError):
        correct_event_quantity(session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("60"),
                               actor_user_id=staff_user.user_id, reason="x")
    with pytest.raises(InvalidEventStructureError, match="Alasan"):
        correct_event_quantity(session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("60"),
                               actor_user_id=pm, reason=" ")
    with pytest.raises(InvalidEventStructureError, match="lebih besar dari nol"):
        correct_event_quantity(session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("0"),
                               actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="tidak ditemukan"):
        correct_event_quantity(session, event_id=999999, link_id=link.link_id, new_quantity=Q("60"),
                               actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="sama"):
        correct_event_quantity(session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("50"),
                               actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="bukan bagian"):
        correct_event_quantity(session, event_id=ev.event_id, link_id=999999, new_quantity=Q("60"),
                               actor_user_id=pm, reason="x")

    cancel_event(session, event_id=ev.event_id, actor_user_id=pm, reason="dibatalkan dulu")
    with pytest.raises(InvalidEventStructureError, match="sudah dibatalkan"):
        correct_event_quantity(session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("60"),
                               actor_user_id=pm, reason="x")


def test_correct_quantity_blocked_by_downstream(session, staff_user, production_manager, supplier):
    ev, batch = _received_batch(session, staff_user, supplier, qty=Q("100"))
    record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))
    link = _output_link(session, ev.event_id, batch.batch_id)
    with pytest.raises(InvalidEventStructureError, match="turunan"):
        correct_event_quantity(session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("60"),
                               actor_user_id=production_manager.user_id, reason="x")


def test_list_event_links(session, staff_user, production_manager, supplier):
    _, batch = _received_batch(session, staff_user, supplier, qty=Q("100"))
    sort_ev = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))
    rows = list_event_links(session, event_id=sort_ev.event_id)
    roles = {(r.role, r.quantity) for r in rows}
    assert (LinkRole.INPUT.value, Q("100")) in roles
    assert (LinkRole.OUTPUT.value, Q("100")) in roles


# --------------------------------------------------------------- blocking chain

def test_blocking_chain_multi_level_and_manual_cascade(session, staff_user, production_manager, supplier):
    receiving_ev, batch = _received_batch(session, staff_user, supplier, qty=Q("100"))
    sort_ev = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff_user.user_id, batch_id=batch.batch_id,
        eg_qty=Q("100")))
    eg_batch = session.query(Batch).filter_by(created_from_event_id=sort_ev.event_id).one()
    qc_ev = record_qc_test(session, QCTestInput(
        event_date=dt.date(2026, 9, 6), pic_user_id=staff_user.user_id, batch_id=eg_batch.batch_id,
        stage=QCStage.IP, ka_1=Q("30.0")))
    pm = production_manager.user_id

    # QC ev sendiri tidak punya turunan -> sudah bisa dikoreksi/dibatalkan langsung.
    qc_chain = get_blocking_chain(session, event_id=qc_ev.event_id)
    assert qc_chain.blocked is False and qc_chain.chain == []

    # Sortasi diblokir oleh QC.
    sort_chain = get_blocking_chain(session, event_id=sort_ev.event_id)
    assert sort_chain.blocked is True
    assert [r.event_id for r in sort_chain.chain] == [qc_ev.event_id]

    # Receiving diblokir TRANSITIF oleh Sortasi *dan* QC, terbaru (event_id
    # terbesar) dulu -- urutan ini valid untuk dibatalkan satu-satu.
    recv_chain = get_blocking_chain(session, event_id=receiving_ev.event_id)
    assert recv_chain.blocked is True
    assert [r.event_id for r in recv_chain.chain] == [qc_ev.event_id, sort_ev.event_id]

    with pytest.raises(InvalidEventStructureError, match="turunan"):
        cancel_event(session, event_id=receiving_ev.event_id, actor_user_id=pm, reason="coba langsung")

    # Cascade manual bertahap: batalkan sesuai urutan chain, index demi index.
    cancel_event(session, event_id=recv_chain.chain[0].event_id, actor_user_id=pm, reason="batal QC dulu")
    sort_chain_after = get_blocking_chain(session, event_id=sort_ev.event_id)
    assert sort_chain_after.blocked is False

    cancel_event(session, event_id=recv_chain.chain[1].event_id, actor_user_id=pm, reason="lalu batal sortasi")
    recv_chain_after = get_blocking_chain(session, event_id=receiving_ev.event_id)
    assert recv_chain_after.blocked is False

    # Sekarang Receiving sendiri sudah bisa dikoreksi/dibatalkan.
    correct_event_date(session, event_id=receiving_ev.event_id, new_event_date=dt.date(2026, 9, 2),
                       actor_user_id=pm, reason="akhirnya bisa dikoreksi setelah cascade manual")
    assert session.get(ProcessEvent, receiving_ev.event_id).event_date == dt.date(2026, 9, 2)


def test_blocking_chain_event_not_found(session):
    with pytest.raises(InvalidEventStructureError, match="tidak ditemukan"):
        get_blocking_chain(session, event_id=999999)


def test_history_includes_quantity_correction(session, staff_user, production_manager, supplier):
    ev, batch = _received_batch(session, staff_user, supplier, qty=Q("50"))
    pm = production_manager.user_id
    link = _output_link(session, ev.event_id, batch.batch_id)

    correct_event_date(session, event_id=ev.event_id, new_event_date=dt.date(2026, 9, 2),
                       actor_user_id=pm, reason="koreksi tanggal dulu")
    correct_event_quantity(session, event_id=ev.event_id, link_id=link.link_id, new_quantity=Q("55"),
                           actor_user_id=pm, reason="lalu koreksi kuantitas")

    history = list_event_correction_history(session, event_id=ev.event_id)
    assert [h.kind for h in history] == ["DATE_CORRECTION", "QUANTITY_CORRECTION"]
    assert "50" in history[1].detail and "55" in history[1].detail
