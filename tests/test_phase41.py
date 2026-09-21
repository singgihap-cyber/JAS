"""Fase 41 -- konfirmasi massal retur supplier (keputusan user 2026-09-21):
centang di tabel UI, hanya Production Manager, satu tanggal terima, semua-atau-tidak-sama-sekali."""
import datetime as dt
from decimal import Decimal as D

import pytest

from traceability_engine.enums import BatchType, EventType
from traceability_engine.exceptions import (
    BulkReturnConfirmError, InvalidEventStructureError, UnauthorizedDispositionError,
)
from traceability_engine.models import AuditLog, ProcessEvent, SupplierReturnHistory, SupplierReturnReceipt
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.supplier_return import (
    bulk_confirm_returns, confirm_return_received, list_supplier_returns,
)

D18 = dt.date(2026, 6, 18)


def _returns(session, staff, supplier, n=3, day=D18):
    for _ in range(n):
        record_receiving(session, ReceivingInput(
            event_date=day, pic_user_id=staff.user_id, supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_KERING, net_quantity=D("100"), off_spec_qty=D("8")))
        day = day + dt.timedelta(days=1)
    return [e.event_id for e in session.query(ProcessEvent)
            .filter_by(event_type=EventType.SUPPLIER_RETURN).order_by(ProcessEvent.event_id)]


def test_bulk_confirms_all_with_history_and_audit(session, staff_user, production_manager, supplier):
    ids = _returns(session, staff_user, supplier)
    receipts = bulk_confirm_returns(session, event_ids=ids, received_date=dt.date(2026, 7, 1),
                                    actor_user_id=production_manager.user_id, note="konfirmasi lama")
    assert len(receipts) == 3
    assert {r.status for r in list_supplier_returns(session)} == {"DITERIMA"}
    assert session.query(SupplierReturnHistory).filter_by(action="CONFIRMED").count() == 3
    assert session.query(AuditLog).filter(AuditLog.after_value.like("%DITERIMA%")).count() == 3


def test_bulk_only_production_manager(session, staff_user, production_manager, supplier):
    ids = _returns(session, staff_user, supplier)
    with pytest.raises(UnauthorizedDispositionError):  # PIC Receiving pencatat pun TIDAK boleh massal
        bulk_confirm_returns(session, event_ids=ids, received_date=dt.date(2026, 7, 1),
                             actor_user_id=staff_user.user_id)
    assert session.query(SupplierReturnReceipt).count() == 0


def test_bulk_all_or_nothing_reports_every_problem(session, staff_user, production_manager, supplier):
    ids = _returns(session, staff_user, supplier)
    confirm_return_received(session, event_id=ids[0], received_date=dt.date(2026, 7, 1),
                            actor_user_id=production_manager.user_id)  # sudah DITERIMA
    with pytest.raises(BulkReturnConfirmError) as ei:
        bulk_confirm_returns(session, event_ids=ids + [99999], received_date=dt.date(2026, 6, 19),
                             actor_user_id=production_manager.user_id)
    bad = {f["event_id"]: f["reason"] for f in ei.value.failures}
    assert set(bad) == {ids[0], ids[2], 99999}  # sudah diterima; ids[2] dikirim 20/6 > terima 19/6; bukan retur
    assert session.query(SupplierReturnReceipt).count() == 1  # ids[1] TIDAK tersimpan
    assert session.query(SupplierReturnHistory).filter_by(action="CONFIRMED").count() == 1


def test_bulk_dedupes_and_rejects_empty(session, staff_user, production_manager, supplier):
    ids = _returns(session, staff_user, supplier, n=2)
    receipts = bulk_confirm_returns(session, event_ids=[ids[0], ids[0], ids[1]],
                                    received_date=dt.date(2026, 7, 1), actor_user_id=production_manager.user_id)
    assert len(receipts) == 2
    with pytest.raises(InvalidEventStructureError, match="minimal satu"):
        bulk_confirm_returns(session, event_ids=[], received_date=dt.date(2026, 7, 1),
                             actor_user_id=production_manager.user_id)
