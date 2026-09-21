"""Fase 33 -- pewarisan AA sepanjang rute Hijau: prefill + peringatan (tidak memblokir)."""
import datetime as dt
from decimal import Decimal

from traceability_engine.enums import BatchType
from traceability_engine.models import Batch
from traceability_engine.services.batch_numbering import (
    aa_inheritance_prefix, aa_inheritance_warning, inherited_jenis, sortation_aa_warnings,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation

D = dt.date(2026, 9, 21)


def _hijau(session, user, supplier, jenis="02"):
    ev = record_receiving(session, ReceivingInput(
        event_date=dt.date(2026, 9, 1), pic_user_id=user.user_id,
        supplier_id=supplier.supplier_id, batch_type=BatchType.RAW_HIJAU,
        net_quantity=Decimal("100"), jenis_code=jenis, grade_code="00"))
    return session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


def test_inherited_jenis_official_and_legacy(session, staff_user, supplier):
    src = _hijau(session, staff_user, supplier)  # 0200024-260901-00
    assert inherited_jenis(src) == {"jenis_code": "02", "jenis_label": "Planifolia"}
    assert aa_inheritance_prefix(src, "02") == "0202024-"
    src.jenis_code = "04"  # legacy intake Hijau
    assert inherited_jenis(src) is None
    assert aa_inheritance_prefix(src, "02") is None
    src.jenis_code = None
    assert inherited_jenis(src) is None


def test_warning_only_when_aa_differs(session, staff_user, supplier):
    src = _hijau(session, staff_user, supplier)
    assert aa_inheritance_warning(src, "0202024-260918-00") is None  # sama
    w = aa_inheritance_warning(src, "0102024-260918-00")
    assert w and "AA 01" in w and "Planifolia" in w
    assert aa_inheritance_warning(src, "") is None
    assert aa_inheritance_warning(src, "ngawur") is None  # tak terurai: lenient


def test_legacy_source_never_warns(session, staff_user, supplier):
    src = _hijau(session, staff_user, supplier)
    src.jenis_code = "04"
    assert aa_inheritance_warning(src, "030218-260618-00") is None  # 04 -> 03 lama


def test_sortation_pp00_typed_aa_change_warns_but_saves(session, staff_user, supplier):
    src = _hijau(session, staff_user, supplier)
    data = SortationInput(
        event_date=D, pic_user_id=staff_user.user_id, batch_id=src.batch_id,
        eg_qty=Decimal("60"), gourmet_qty=Decimal("30"), process_code="00",
        eg_batch_number="0102024-260918-00", gourmet_batch_number="0201024-260918-00")
    record_sortation(session, data)  # tidak diblokir
    assert session.query(Batch).filter_by(batch_number="0102024-260918-00").count() == 1
    warnings = sortation_aa_warnings(src, data)
    assert len(warnings) == 1 and "0102024-260918-00" in warnings[0]
