"""Fase 50 -- penutupan backlog (keputusan diserahkan user ke Claude, 2026-09-23).

1. forward/backward trace menyaring event VOID secara default (include_void=True
   = perilaku lama).
2. Penyeimbangan susut event lama yang tidak seimbang: susut = input - output -
   loss (aturan Fase 49 poin 1 diterapkan surut), satu per satu atau massal.
3. Susut negatif hasil penyeimbangan tidak ditolak, hanya diperingatkan.
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from traceability_engine.enums import BatchStatus, BatchType, LinkRole
from traceability_engine.exceptions import InvalidEventStructureError, UnauthorizedDispositionError
from traceability_engine.models import AuditLog, Batch, EventBatchLink, EventShrinkageCorrection, ProcessEvent
from traceability_engine.services.event_correction import (
    cancel_event, list_event_correction_history, list_unbalanced_events, rebalance_all_unbalanced,
    rebalance_event_shrinkage,
)
from traceability_engine.services.genealogy import backward_trace, forward_trace
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.services.steam_dry import SundryingInput, record_sundrying
from traceability_engine.services.stock import reconcile_batch
from traceability_engine.services.traceability import chain_of_custody_report

Q = Decimal


def _received(session, staff, supplier, qty=Q("100"), day=1):
    ev = record_receiving(session, ReceivingInput(
        event_date=dt.date(2026, 9, day), pic_user_id=staff.user_id, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_KERING, net_quantity=qty, jenis_code="02", grade_code="00"))
    return session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


def _sorted(session, staff, batch):
    ev = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 9, 5), pic_user_id=staff.user_id, batch_id=batch.batch_id,
        initial_qty=Q("50"), eg_qty=Q("45")))
    out = session.execute(select(EventBatchLink).where(
        EventBatchLink.event_id == ev.event_id, EventBatchLink.role == LinkRole.OUTPUT,
        EventBatchLink.batch_id != batch.batch_id)).scalars().first()
    return ev, session.get(Batch, out.batch_id)


def _sundried(session, staff, supplier, day=1):
    batch = _received(session, staff, supplier, day=day)
    sd = record_sundrying(session, SundryingInput(
        event_date=dt.date(2026, 9, 3), pic_user_id=staff.user_id, batch_id=batch.batch_id,
        starting_quantity=Q("100"), final_quantity=Q("80")))
    return sd, batch


# ------------------------------------------------ 1. trace menyaring VOID

def test_forward_trace_skips_void_event(session, staff_user, production_manager, supplier):
    batch = _received(session, staff_user, supplier)
    sort_ev, child = _sorted(session, staff_user, batch)
    assert [c.batch.batch_id for c in forward_trace(session, batch.batch_id).children] == [child.batch_id]

    cancel_event(session, event_id=sort_ev.event_id, actor_user_id=production_manager.user_id, reason="salah")
    node = forward_trace(session, batch.batch_id)
    assert node.children == [] and node.applied_events == []
    full = forward_trace(session, batch.batch_id, include_void=True)
    assert [c.batch.batch_id for c in full.children] == [child.batch_id]


def test_backward_trace_marks_voided_origin(session, staff_user, production_manager, supplier):
    batch = _received(session, staff_user, supplier)
    sort_ev, child = _sorted(session, staff_user, batch)
    cancel_event(session, event_id=sort_ev.event_id, actor_user_id=production_manager.user_id, reason="salah")

    node = backward_trace(session, child.batch_id)
    assert node.voided_origin is True and node.parents == []
    assert node.produced_by_event.event_id == sort_ev.event_id
    full = backward_trace(session, child.batch_id, include_void=True)
    assert full.voided_origin is False and [p.batch.batch_id for p in full.parents] == [batch.batch_id]


def test_report_counts_hidden_void_events(session, staff_user, production_manager, supplier):
    batch = _received(session, staff_user, supplier)
    sort_ev, _ = _sorted(session, staff_user, batch)
    cancel_event(session, event_id=sort_ev.event_id, actor_user_id=production_manager.user_id, reason="salah")

    rep = chain_of_custody_report(session, batch.batch_id)
    assert rep["void_events_excluded"] == 1 and rep["downstream_events"] == []
    # batch sumber kembali jadi daun yang masih dalam proses (stoknya sudah dikembalikan)
    assert [b["batch_id"] for b in rep["incomplete_leaves"]] == [batch.batch_id]
    rep_all = chain_of_custody_report(session, batch.batch_id, include_void=True)
    assert [e["status"] for e in rep_all["downstream_events"]] == ["VOID"]


def test_active_trace_unchanged(session, staff_user, supplier):
    batch = _received(session, staff_user, supplier)
    _, child = _sorted(session, staff_user, batch)
    rep = chain_of_custody_report(session, child.batch_id)
    assert rep["voided_origin"] is False and rep["void_events_excluded"] == 0
    assert [e["event_type"] for e in rep["upstream_events"]] == ["RECEIVING", "SORTATION"]
    assert rep["suppliers"][0]["supplier_id"] == supplier.supplier_id


# ------------------------------------------------ 2. penyeimbangan susut

def test_rebalance_sets_shrinkage_from_links(session, staff_user, production_manager, supplier):
    sd, batch = _sundried(session, staff_user, supplier)
    sd.shrinkage_qty = Q("17")  # era Fase 45: susut tidak ikut dikoreksi
    session.flush()
    assert [r.event_id for r in list_unbalanced_events(session)] == [sd.event_id]

    res = rebalance_event_shrinkage(
        session, event_id=sd.event_id, actor_user_id=production_manager.user_id, reason="koreksi lama")
    assert res.warnings == []
    assert session.get(ProcessEvent, sd.event_id).shrinkage_qty == Q("20")
    assert res.entry.source == "REBALANCE" and res.entry.old_shrinkage_qty == Q("17")
    assert list_unbalanced_events(session) == []
    assert reconcile_batch(session, batch.batch_id).matches  # stok tidak disentuh
    hist = list_event_correction_history(session, event_id=sd.event_id)
    assert "penyeimbangan susut" in hist[-1].detail
    assert session.query(AuditLog).filter_by(entity_type="ProcessEvent", entity_id=sd.event_id).count() >= 1


def test_rebalance_keeps_loss_and_allows_downstream(session, staff_user, production_manager, supplier):
    batch = _received(session, staff_user, supplier)
    sort_ev, child = _sorted(session, staff_user, batch)
    record_sundrying(session, SundryingInput(
        event_date=dt.date(2026, 9, 7), pic_user_id=staff_user.user_id, batch_id=child.batch_id,
        starting_quantity=Q("45"), final_quantity=Q("40")))  # sortasi kini punya turunan
    sort_ev.shrinkage_qty, sort_ev.loss_qty = Q("1"), Q("2")
    session.flush()
    rebalance_event_shrinkage(
        session, event_id=sort_ev.event_id, actor_user_id=production_manager.user_id, reason="x")
    ev = session.get(ProcessEvent, sort_ev.event_id)
    assert (ev.shrinkage_qty, ev.loss_qty) == (Q("3"), Q("2"))  # 50 = 45 + 3 + 2


def test_rebalance_negative_warns(session, staff_user, production_manager, supplier):
    sd, _ = _sundried(session, staff_user, supplier)
    sd.shrinkage_qty, sd.loss_qty = Q("0"), Q("25")
    session.flush()
    res = rebalance_event_shrinkage(
        session, event_id=sd.event_id, actor_user_id=production_manager.user_id, reason="x")
    assert session.get(ProcessEvent, sd.event_id).shrinkage_qty == Q("-5")
    assert "negatif" in res.warnings[0]


def test_rebalance_guards(session, staff_user, production_manager, supplier):
    sd, batch = _sundried(session, staff_user, supplier)
    with pytest.raises(UnauthorizedDispositionError):
        rebalance_event_shrinkage(session, event_id=sd.event_id, actor_user_id=staff_user.user_id, reason="x")
    with pytest.raises(InvalidEventStructureError, match="wajib"):
        rebalance_event_shrinkage(session, event_id=sd.event_id, actor_user_id=production_manager.user_id, reason=" ")
    with pytest.raises(InvalidEventStructureError, match="sudah seimbang"):
        rebalance_event_shrinkage(session, event_id=sd.event_id, actor_user_id=production_manager.user_id, reason="x")
    recv_id = batch.created_from_event_id
    with pytest.raises(InvalidEventStructureError, match="tidak punya susut"):
        rebalance_event_shrinkage(session, event_id=recv_id, actor_user_id=production_manager.user_id, reason="x")
    sd.shrinkage_qty = Q("1")
    cancel_event(session, event_id=sd.event_id, actor_user_id=production_manager.user_id, reason="batal")
    with pytest.raises(InvalidEventStructureError, match="VOID"):
        rebalance_event_shrinkage(session, event_id=sd.event_id, actor_user_id=production_manager.user_id, reason="x")


def test_rebalance_all(session, staff_user, production_manager, supplier):
    sd1, b1 = _sundried(session, staff_user, supplier)
    sd2, b2 = _sundried(session, staff_user, supplier, day=2)
    sd1.shrinkage_qty, sd2.shrinkage_qty = Q("10"), Q("30")
    session.flush()
    only_b1 = rebalance_all_unbalanced(
        session, actor_user_id=production_manager.user_id, reason="x", batch_id=b1.batch_id)
    assert [r.entry.event_id for r in only_b1] == [sd1.event_id]
    rest = rebalance_all_unbalanced(session, actor_user_id=production_manager.user_id, reason="x")
    assert [r.entry.event_id for r in rest] == [sd2.event_id]
    assert list_unbalanced_events(session) == []
    assert rebalance_all_unbalanced(session, actor_user_id=production_manager.user_id, reason="x") == []
    with pytest.raises(UnauthorizedDispositionError):
        rebalance_all_unbalanced(session, actor_user_id=staff_user.user_id, reason="x")
