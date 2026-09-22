"""Fase 46 -- koreksi Jenis (AA) + cascade ke turunan, dan koreksi catatan
event historis. Keputusan user 2026-09-23 (AskUserQuestion "Fase 46"):

1. Jenis: ganti jenis + ikut ubah batch turunan; boleh walau ada turunan;
   cascade BERHENTI di Mixing (ditandai, tidak diubah); TOLAK SELURUH koreksi
   bila ada batch yang akan diubah sudah dikirim.
2. Catatan: menimpa isi lama (jejak di riwayat), tetap hanya event tanpa
   turunan (sama dengan Fase 44/45).
"""
import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import (
    BatchStatus, BatchType, EventStatus, EventType, LinkRole,
)
from traceability_engine.exceptions import (
    BatchNumberConflictError, InvalidEventStructureError, UnauthorizedDispositionError,
)
from traceability_engine.models import (
    AuditLog, Batch, BatchNumberCorrection, EventBatchLink, EventNotesCorrection,
    JenisCorrection, ProcessEvent,
)
from traceability_engine.services.event_correction import (
    correct_event_notes, list_correctable_events, list_event_correction_history,
)
from traceability_engine.services.jenis_correction import (
    correct_batch_jenis, list_jenis_corrections, plan_jenis_correction,
)

Q = Decimal
D = dt.date(2026, 9, 1)


def _batch(session, number, status=BatchStatus.ACTIVE, supplier=None, qty=Q("10")):
    parts = {}
    if number:
        from traceability_engine import batch_number as bn
        p = bn.parse(number)
        parts = dict(jenis_code=p.jenis_code, grade_code=p.grade_code,
                     supplier_code=p.supplier_code, receiving_date=p.receiving_date,
                     process_code=p.process_code)
    b = Batch(batch_number=number, batch_type=BatchType.PROCESSED, status=status,
              current_quantity=qty, supplier_id=supplier.supplier_id if supplier else None, **parts)
    session.add(b)
    session.flush()
    return b


def _event(session, etype, pic, inputs=(), outputs=(), status=EventStatus.COMPLETED, notes=None,
           date=D):
    ev = ProcessEvent(event_type=etype, event_date=date, pic_user_id=pic.user_id,
                      status=status, notes=notes)
    session.add(ev)
    session.flush()
    for b in inputs:
        session.add(EventBatchLink(event_id=ev.event_id, batch_id=b.batch_id,
                                   role=LinkRole.INPUT, quantity=Q("1")))
    for b in outputs:
        session.add(EventBatchLink(event_id=ev.event_id, batch_id=b.batch_id,
                                   role=LinkRole.OUTPUT, quantity=Q("1")))
    session.flush()
    return ev


@pytest.fixture()
def chain(session, staff_user, supplier):
    """Root (02) -> Sortasi -> A (02), B (01, jenis sudah berbeda).
    A -> QC (self-loop) ; A -> Rework -> C (02) ; A + X(asing) -> Sortasi -> M2 (sumber lain);
    A + Y -> Mixing -> M (hasil Mixing)."""
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    _event(session, EventType.RECEIVING, staff_user, outputs=[root])
    a = _batch(session, "0201024-260901-01", supplier=supplier)
    b = _batch(session, "0102024-260901-01", supplier=supplier)
    _event(session, EventType.SORTATION, staff_user, inputs=[root], outputs=[a, b])
    _event(session, EventType.QC_TEST, staff_user, inputs=[a], outputs=[a])
    c = _batch(session, "0201024-260901-04", supplier=supplier)
    _event(session, EventType.REWORK, staff_user, inputs=[a], outputs=[c])
    x = _batch(session, "0205024-260801-00", supplier=supplier)
    m2 = _batch(session, "0203024-260901-02", supplier=supplier)
    _event(session, EventType.SORTATION, staff_user, inputs=[a, x], outputs=[m2])
    y = _batch(session, "0201000-260901-03")
    m = _batch(session, "0201000-260902-03")
    _event(session, EventType.MIXING, staff_user, inputs=[a, y], outputs=[m])
    return dict(root=root, a=a, b=b, c=c, x=x, m2=m2, y=y, m=m)


def test_plan_cascades_and_stops(session, chain):
    plan = plan_jenis_correction(session, batch_id=chain["root"].batch_id, new_jenis_code="01")
    assert plan.ok and plan.old_jenis_code == "02"
    changed = {r.batch_id: r.new_batch_number for r in plan.changes}
    assert changed == {
        chain["root"].batch_id: "0100024-260901-00",
        chain["a"].batch_id: "0101024-260901-01",
        chain["c"].batch_id: "0101024-260901-04",
    }
    stops = {r.batch_id: r.note for r in plan.stops}
    assert "jenis sudah berbeda" in stops[chain["b"].batch_id]
    assert "hasil Mixing" in stops[chain["m"].batch_id]
    assert "sumber lain" in stops[chain["m2"].batch_id]


def test_correct_jenis_applies_cascade(session, staff_user, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    _event(session, EventType.RECEIVING, staff_user, outputs=[root])
    a = _batch(session, "0201024-260901-01", supplier=supplier)
    _event(session, EventType.SORTATION, staff_user, inputs=[root], outputs=[a])
    c = _batch(session, "0201024-260901-04", supplier=supplier)
    _event(session, EventType.REWORK, staff_user, inputs=[a], outputs=[c])
    y = _batch(session, "0201000-260901-03")
    m = _batch(session, "0201000-260902-03")
    _event(session, EventType.MIXING, staff_user, inputs=[c, y], outputs=[m])

    entry, plan = correct_batch_jenis(
        session, batch_id=root.batch_id, new_jenis_code="01",
        actor_user_id=production_manager.user_id, reason="Salah pilih jenis saat receiving")
    assert [session.get(Batch, i).batch_number for i in (root.batch_id, a.batch_id, c.batch_id)] == [
        "0100024-260901-00", "0101024-260901-01", "0101024-260901-04"]
    assert session.get(Batch, a.batch_id).jenis_code == "01"
    assert session.get(Batch, m.batch_id).batch_number == "0201000-260902-03"  # Mixing tidak diubah
    assert entry.changed_batch_ids == f"{root.batch_id},{a.batch_id},{c.batch_id}"
    assert "hasil Mixing" in entry.stopped_summary
    assert session.query(BatchNumberCorrection).count() == 3
    assert session.query(AuditLog).filter_by(entity_type="JenisCorrection").count() == 1
    assert entry.notice
    assert [j.correction_id for j in list_jenis_corrections(session, batch_id=c.batch_id)] == [entry.correction_id]
    assert list_jenis_corrections(session, batch_id=m.batch_id) == []


def test_shipped_descendant_rejects_whole(session, staff_user, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    a = _batch(session, "0201024-260901-01", supplier=supplier, status=BatchStatus.SHIPPED)
    _event(session, EventType.SORTATION, staff_user, inputs=[root], outputs=[a])
    with pytest.raises(InvalidEventStructureError, match="SHIPPED"):
        correct_batch_jenis(session, batch_id=root.batch_id, new_jenis_code="01",
                            actor_user_id=production_manager.user_id, reason="x")
    assert session.get(Batch, root.batch_id).batch_number == "0200024-260901-00"
    assert session.query(JenisCorrection).count() == 0


def test_partial_delivery_counts_as_shipped(session, staff_user, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    _event(session, EventType.DELIVERY, staff_user, inputs=[root])
    plan = plan_jenis_correction(session, batch_id=root.batch_id, new_jenis_code="01")
    assert not plan.ok and "sudah dikirim" in plan.blockers[0].note


def test_void_event_ignored(session, staff_user, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    a = _batch(session, "0201024-260901-01", supplier=supplier)
    _event(session, EventType.SORTATION, staff_user, inputs=[root], outputs=[a], status=EventStatus.VOID)
    _event(session, EventType.DELIVERY, staff_user, inputs=[root], status=EventStatus.VOID)
    plan = plan_jenis_correction(session, batch_id=root.batch_id, new_jenis_code="01")
    assert plan.ok and [r.batch_id for r in plan.changes] == [root.batch_id]


def test_legacy_root_to_official(session, production_manager, supplier):
    root = _batch(session, "0300024-260901-00", supplier=supplier)
    entry, _ = correct_batch_jenis(session, batch_id=root.batch_id, new_jenis_code="02",
                                   actor_user_id=production_manager.user_id, reason="kode lama")
    assert session.get(Batch, root.batch_id).batch_number == "0200024-260901-00"
    assert entry.old_jenis_code == "03"


def test_jenis_guards(session, staff_user, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    unnumbered = _batch(session, None)
    pm = production_manager.user_id
    with pytest.raises(UnauthorizedDispositionError):
        correct_batch_jenis(session, batch_id=root.batch_id, new_jenis_code="01",
                            actor_user_id=staff_user.user_id, reason="x")
    with pytest.raises(InvalidEventStructureError, match="Alasan"):
        correct_batch_jenis(session, batch_id=root.batch_id, new_jenis_code="01", actor_user_id=pm, reason=" ")
    with pytest.raises(InvalidEventStructureError, match="sama"):
        correct_batch_jenis(session, batch_id=root.batch_id, new_jenis_code="02", actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="tidak valid"):
        correct_batch_jenis(session, batch_id=root.batch_id, new_jenis_code="03", actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="belum punya nomor"):
        correct_batch_jenis(session, batch_id=unnumbered.batch_id, new_jenis_code="01", actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="tidak ditemukan"):
        correct_batch_jenis(session, batch_id=9999, new_jenis_code="01", actor_user_id=pm, reason="x")


def test_clash_raises_conflict(session, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    _batch(session, "0100024-260901-00", supplier=supplier)
    plan = plan_jenis_correction(session, batch_id=root.batch_id, new_jenis_code="01")
    assert not plan.ok and "sudah dipakai" in plan.blockers[0].note
    with pytest.raises(BatchNumberConflictError):
        correct_batch_jenis(session, batch_id=root.batch_id, new_jenis_code="01",
                            actor_user_id=production_manager.user_id, reason="x")
    assert session.get(Batch, root.batch_id).jenis_code == "02"


# ------------------------------------------------------------- catatan event
def test_correct_notes_overwrites_with_history(session, staff_user, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    ev = _event(session, EventType.RECEIVING, staff_user, outputs=[root], notes="Tgl selesai: 2026-09-03")
    pm = production_manager.user_id
    entry = correct_event_notes(session, event_id=ev.event_id, new_notes="Tgl selesai: 2026-09-04",
                                actor_user_id=pm, reason="salah ketik tanggal selesai")
    assert session.get(ProcessEvent, ev.event_id).notes == "Tgl selesai: 2026-09-04"
    assert entry.old_notes == "Tgl selesai: 2026-09-03"
    correct_event_notes(session, event_id=ev.event_id, new_notes="  ", actor_user_id=pm, reason="hapus")
    assert session.get(ProcessEvent, ev.event_id).notes is None
    assert session.query(EventNotesCorrection).filter_by(event_id=ev.event_id).count() == 2
    kinds = [h.kind for h in list_event_correction_history(session, event_id=ev.event_id)]
    assert kinds == ["NOTES_CORRECTION", "NOTES_CORRECTION"]
    assert session.query(AuditLog).filter_by(entity_type="ProcessEvent", entity_id=ev.event_id).count() == 2


def test_correct_notes_guards(session, staff_user, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    ev = _event(session, EventType.RECEIVING, staff_user, outputs=[root], notes="a")
    pm = production_manager.user_id
    with pytest.raises(UnauthorizedDispositionError):
        correct_event_notes(session, event_id=ev.event_id, new_notes="b", actor_user_id=staff_user.user_id, reason="x")
    with pytest.raises(InvalidEventStructureError, match="Alasan"):
        correct_event_notes(session, event_id=ev.event_id, new_notes="b", actor_user_id=pm, reason="")
    with pytest.raises(InvalidEventStructureError, match="sama"):
        correct_event_notes(session, event_id=ev.event_id, new_notes=" a ", actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="tidak ditemukan"):
        correct_event_notes(session, event_id=9999, new_notes="b", actor_user_id=pm, reason="x")
    # sudah punya turunan -> diblokir (sama dengan Fase 44/45)
    a = _batch(session, "0201024-260901-01", supplier=supplier)
    _event(session, EventType.SORTATION, staff_user, inputs=[root], outputs=[a])
    with pytest.raises(InvalidEventStructureError, match="turunan"):
        correct_event_notes(session, event_id=ev.event_id, new_notes="b", actor_user_id=pm, reason="x")
    void = _event(session, EventType.QC_TEST, staff_user, inputs=[a], outputs=[a], status=EventStatus.VOID)
    with pytest.raises(InvalidEventStructureError, match="VOID"):
        correct_event_notes(session, event_id=void.event_id, new_notes="b", actor_user_id=pm, reason="x")


def test_correctable_rows_expose_notes(session, staff_user, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    ev = _event(session, EventType.RECEIVING, staff_user, outputs=[root], notes="catatan awal")
    rows = {r.event_id: r for r in list_correctable_events(session)}
    assert rows[ev.event_id].notes == "catatan awal"


def test_correct_notes_keeps_json_structure(session, staff_user, production_manager, supplier):
    root = _batch(session, "0200024-260901-00", supplier=supplier)
    ev = _event(session, EventType.SORTATION, staff_user, inputs=[root], notes='{"end_date": "2026-09-03"}')
    pm = production_manager.user_id
    with pytest.raises(InvalidEventStructureError, match="terstruktur"):
        correct_event_notes(session, event_id=ev.event_id, new_notes="selesai tgl 4", actor_user_id=pm, reason="x")
    with pytest.raises(InvalidEventStructureError, match="bukan objek"):
        correct_event_notes(session, event_id=ev.event_id, new_notes="[1, 2]", actor_user_id=pm, reason="x")
    correct_event_notes(session, event_id=ev.event_id, new_notes='{"end_date": "2026-09-04"}',
                        actor_user_id=pm, reason="tanggal selesai sortasi salah")
    assert session.get(ProcessEvent, ev.event_id).notes == '{"end_date": "2026-09-04"}'
    # mengosongkan tetap boleh
    correct_event_notes(session, event_id=ev.event_id, new_notes="", actor_user_id=pm, reason="hapus")
    assert session.get(ProcessEvent, ev.event_id).notes is None
