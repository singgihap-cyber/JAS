"""Fase 40 -- role konfirmasi retur, pembatalan (hanya Production Manager), riwayat.
Keputusan user 2026-09-21."""
import datetime as dt
from decimal import Decimal as D

import pytest

from traceability_engine.enums import BatchType, EventType, UserRole
from traceability_engine.exceptions import InvalidEventStructureError, UnauthorizedDispositionError
from traceability_engine.models import AuditLog, ProcessEvent, SupplierReturnHistory, SupplierReturnReceipt, User
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.supplier_return import (
    cancel_return_confirmation, confirm_return_received, list_return_history, list_supplier_returns,
)

D18 = dt.date(2026, 6, 18)


def _return(session, recorder, supplier):
    record_receiving(session, ReceivingInput(
        event_date=D18, pic_user_id=recorder.user_id, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_KERING, net_quantity=D("100"), off_spec_qty=D("8")))
    return session.query(ProcessEvent).filter_by(event_type=EventType.SUPPLIER_RETURN).one()


def _other_staff(session):
    u = User(name="Lain", role=UserRole.STAFF)
    session.add(u)
    session.flush()
    return u


def test_only_manager_or_receiving_recorder_may_confirm(session, staff_user, production_manager, supplier):
    ev = _return(session, staff_user, supplier)
    outsider = _other_staff(session)
    with pytest.raises(UnauthorizedDispositionError):
        confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=outsider.user_id)
    assert session.query(SupplierReturnReceipt).count() == 0
    confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=staff_user.user_id)
    assert session.query(SupplierReturnReceipt).count() == 1


def test_manager_may_confirm_any_return(session, staff_user, production_manager, supplier):
    ev = _return(session, staff_user, supplier)
    confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=production_manager.user_id)
    [row] = list_supplier_returns(session)
    assert row.status == "DITERIMA"


def test_cancel_only_manager_with_reason(session, staff_user, production_manager, supplier):
    ev = _return(session, staff_user, supplier)
    confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=staff_user.user_id)
    with pytest.raises(UnauthorizedDispositionError):  # PIC Receiving TIDAK boleh membatalkan
        cancel_return_confirmation(session, event_id=ev.event_id, actor_user_id=staff_user.user_id, reason="salah")
    with pytest.raises(InvalidEventStructureError, match="alasan"):
        cancel_return_confirmation(session, event_id=ev.event_id, actor_user_id=production_manager.user_id, reason="  ")
    assert list_supplier_returns(session)[0].status == "DITERIMA"


def test_cancel_returns_to_dikirim_keeps_history_and_allows_reconfirm(session, staff_user, production_manager, supplier):
    ev = _return(session, staff_user, supplier)
    confirm_return_received(session, event_id=ev.event_id, received_date=D18,
                            actor_user_id=staff_user.user_id, note="salah tekan")
    cancel_return_confirmation(session, event_id=ev.event_id,
                               actor_user_id=production_manager.user_id, reason="Salah konfirmasi")
    [row] = list_supplier_returns(session, as_of=dt.date(2026, 6, 28))
    assert row.status == "DIKIRIM" and row.overdue and row.days_outstanding == 10  # dihitung dari tanggal kirim
    assert session.query(SupplierReturnReceipt).count() == 0
    with pytest.raises(InvalidEventStructureError, match="tidak berstatus"):  # batal ganda ditolak
        cancel_return_confirmation(session, event_id=ev.event_id, actor_user_id=production_manager.user_id, reason="x")
    confirm_return_received(session, event_id=ev.event_id, received_date=dt.date(2026, 6, 20),
                            actor_user_id=staff_user.user_id)  # konfirmasi ulang boleh
    assert list_supplier_returns(session)[0].received_date == dt.date(2026, 6, 20)
    hist = list_return_history(session, event_id=ev.event_id)
    assert [h.action for h in hist] == ["CONFIRMED", "CANCELLED", "CONFIRMED"]
    assert hist[1].note == "Salah konfirmasi" and hist[1].actor_user_id == production_manager.user_id
    assert hist[0].note == "salah tekan"
    assert session.query(AuditLog).filter(AuditLog.after_value.like("SUPPLIER_RETURN DIKIRIM (konfirmasi dibatalkan%")).count() == 1


def test_phase38_confirmation_without_history_is_snapshotted_on_cancel(session, staff_user, production_manager, supplier):
    ev = _return(session, staff_user, supplier)
    session.add(SupplierReturnReceipt(return_event_id=ev.event_id, received_date=D18,
                                      confirmed_by=staff_user.user_id, note="lama"))  # tanpa riwayat (pra-Fase 40)
    session.flush()
    cancel_return_confirmation(session, event_id=ev.event_id, actor_user_id=production_manager.user_id, reason="koreksi")
    assert [h.action for h in list_return_history(session, event_id=ev.event_id)] == ["CONFIRMED", "CANCELLED"]
