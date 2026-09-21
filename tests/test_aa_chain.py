"""Fase 36 -- laporan audit perubahan AA di rute Hijau (read-only)."""
import datetime as dt
from decimal import Decimal

from traceability_engine.enums import BatchType
from traceability_engine.models import Batch
from traceability_engine.services.aa_chain import audit_aa_chain
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation

D = dt.date(2026, 9, 21)


def _receive(session, user, supplier, btype, jenis, grade="00", day=1):
    ev = record_receiving(session, ReceivingInput(
        event_date=dt.date(2026, 9, day), pic_user_id=user.user_id,
        supplier_id=supplier.supplier_id, batch_type=btype,
        net_quantity=Decimal("100"), jenis_code=jenis, grade_code=grade))
    return session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


def _sort(session, user, src, **numbers):
    return record_sortation(session, SortationInput(
        event_date=D, pic_user_id=user.user_id, batch_id=src.batch_id,
        eg_qty=Decimal("60"), gourmet_qty=Decimal("30"), process_code="00", **numbers))


def test_no_findings_when_aa_consistent(session, staff_user, supplier):
    src = _receive(session, staff_user, supplier, BatchType.RAW_HIJAU, "02")
    _sort(session, staff_user, src, eg_batch_number="0202024-260918-00",
          gourmet_batch_number="0201024-260918-00")
    assert audit_aa_chain(session) == []


def test_aa_change_on_hijau_route_reported(session, staff_user, supplier):
    src = _receive(session, staff_user, supplier, BatchType.RAW_HIJAU, "02")
    _sort(session, staff_user, src, eg_batch_number="0102024-260918-00",
          gourmet_batch_number="0201024-260918-00")
    rows = audit_aa_chain(session, hijau_only=True)
    assert len(rows) == 1
    r = rows[0]
    assert r.event_type == "SORTATION" and r.hijau_route
    assert (r.source_jenis_code, r.result_jenis_code) == ("02", "01")
    assert r.result_batch_number == "0102024-260918-00" and "Planifolia" in r.message()
    assert audit_aa_chain(session, batch_id=r.result_batch_id) == rows
    assert audit_aa_chain(session, batch_id=999) == []


def test_kering_route_only_when_not_hijau_only(session, staff_user, supplier):
    src = _receive(session, staff_user, supplier, BatchType.RAW_KERING, "02", "02")
    _sort(session, staff_user, src, eg_batch_number="0102024-260918-00")
    assert audit_aa_chain(session, hijau_only=True) == []
    rows = audit_aa_chain(session, hijau_only=False)
    assert len(rows) == 1 and rows[0].hijau_route is False


def test_legacy_source_skipped_and_descendants_flagged_hijau(session, staff_user, supplier):
    src = _receive(session, staff_user, supplier, BatchType.RAW_HIJAU, "02")
    src.jenis_code = "04"  # legacy intake Hijau: dilewati
    _sort(session, staff_user, src, eg_batch_number="0302024-260918-00")
    assert audit_aa_chain(session, hijau_only=False) == []
    # turunan dua tingkat: sumber sah 02 hasil sortasi, lalu sortasi lagi dgn AA salah
    src2 = _receive(session, staff_user, supplier, BatchType.RAW_HIJAU, "02", day=2)
    _sort(session, staff_user, src2, eg_batch_number="0202024-260919-00")
    mid = session.query(Batch).filter_by(batch_number="0202024-260919-00").one()
    _sort(session, staff_user, mid, eg_batch_number="0102024-260920-00")
    rows = audit_aa_chain(session, hijau_only=True)
    assert len(rows) == 1 and rows[0].source_batch_id == mid.batch_id and rows[0].hijau_route
