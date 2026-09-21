"""Fase 38 -- tiga aturan bisnis yang tadinya terbuka (keputusan user 2026-09-21):
(1) on_spec + off_spec = netto (blokir, tanpa toleransi);
(2) status retur supplier DIKIRIM -> DITERIMA;
(3) tanggal MULAI sortasi wajib (end_date tidak boleh lebih awal).
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from traceability_engine.enums import BatchType, EventStatus, EventType
from traceability_engine.exceptions import InvalidEventStructureError
from traceability_engine.models import AuditLog, Batch, ProcessEvent, SupplierReturnReceipt
from traceability_engine.services.adjustment import mark_batch_rejected
from traceability_engine.services.disposition import return_to_supplier
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.services.supplier_return import (
    confirm_return_received, list_supplier_returns,
)

D18 = dt.date(2026, 6, 18)


def _recv(session, uid, supplier, net=D("100"), **kw):
    return record_receiving(session, ReceivingInput(
        event_date=D18, pic_user_id=uid, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_KERING, net_quantity=net, **kw))


# ------------------------------------------------ (1) on_spec + off_spec = netto
def test_on_plus_off_must_equal_net_exactly(session, staff_user, supplier):
    for on, off in ((D("91"), D("8")), (D("92.001"), D("8")), (D("90"), D("10.5"))):
        with pytest.raises(ValueError, match="harus sama persis"):
            _recv(session, staff_user.user_id, supplier, on_spec_qty=on, off_spec_qty=off)
    assert session.query(ProcessEvent).count() == 0  # tidak ada yang tersimpan


def test_on_plus_off_equal_net_is_accepted(session, staff_user, supplier):
    _recv(session, staff_user.user_id, supplier, on_spec_qty=D("92"), off_spec_qty=D("8"))
    b = session.query(Batch).one()
    assert b.current_quantity == D("92")
    _recv(session, staff_user.user_id, supplier, net=D("10"), on_spec_qty=D("10"), off_spec_qty=D("0"))


def test_only_one_of_on_off_is_not_summed(session, staff_user, supplier):
    _recv(session, staff_user.user_id, supplier, off_spec_qty=D("8"))
    _recv(session, staff_user.user_id, supplier, on_spec_qty=D("90"))
    with pytest.raises(ValueError):
        _recv(session, staff_user.user_id, supplier, on_spec_qty=D("-1"))




# ------------------------------------------------ (2) status retur supplier
def _offspec_return(session, uid, supplier):
    _recv(session, uid, supplier, off_spec_qty=D("8"))
    return session.query(ProcessEvent).filter_by(event_type=EventType.SUPPLIER_RETURN).one()


def test_new_return_starts_as_dikirim(session, staff_user, supplier):
    ev = _offspec_return(session, staff_user.user_id, supplier)
    [row] = list_supplier_returns(session, as_of=dt.date(2026, 6, 28))
    assert row.event_id == ev.event_id and row.status == "DIKIRIM"
    assert row.quantity == D("8") and row.source == "RECEIVING_OFF_SPEC"
    assert row.supplier_name == "WARDOYO" and row.days_outstanding == 10
    assert row.received_date is None


def test_confirm_received_moves_to_diterima_and_audits(session, staff_user, supplier):
    ev = _offspec_return(session, staff_user.user_id, supplier)
    stock_before = session.query(Batch).one().current_quantity
    r = confirm_return_received(session, event_id=ev.event_id, received_date=dt.date(2026, 6, 25),
                                actor_user_id=staff_user.user_id, note=" diterima Pak Wardoyo ")
    assert r.received_date == dt.date(2026, 6, 25) and r.note == "diterima Pak Wardoyo"
    [row] = list_supplier_returns(session)
    assert row.status == "DITERIMA" and row.received_date == dt.date(2026, 6, 25)
    assert row.days_outstanding is None and row.confirmed_by == staff_user.user_id
    assert session.query(Batch).one().current_quantity == stock_before  # stok tidak berubah
    log = session.execute(select(AuditLog).where(AuditLog.after_value.like("SUPPLIER_RETURN DITERIMA%"))).scalar_one()
    assert log.entity_id == ev.event_id and log.before_value == "SUPPLIER_RETURN DIKIRIM"


def test_confirm_rules(session, staff_user, supplier):
    ev = _offspec_return(session, staff_user.user_id, supplier)
    uid = staff_user.user_id
    with pytest.raises(InvalidEventStructureError, match="lebih awal"):
        confirm_return_received(session, event_id=ev.event_id, received_date=dt.date(2026, 6, 17), actor_user_id=uid)
    with pytest.raises(InvalidEventStructureError, match="User"):
        confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=9999)
    recv_event = session.query(ProcessEvent).filter_by(event_type=EventType.RECEIVING).one()
    with pytest.raises(InvalidEventStructureError, match="SUPPLIER_RETURN"):
        confirm_return_received(session, event_id=recv_event.event_id, received_date=D18, actor_user_id=uid)
    confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=uid)  # tgl sama boleh
    with pytest.raises(InvalidEventStructureError, match="sudah dikonfirmasi"):
        confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=uid)
    assert session.query(SupplierReturnReceipt).count() == 1


def test_void_event_not_listed_or_confirmable(session, staff_user, supplier):
    ev = _offspec_return(session, staff_user.user_id, supplier)
    ev.status = EventStatus.VOID
    session.flush()
    assert list_supplier_returns(session) == []
    with pytest.raises(InvalidEventStructureError, match="VOID"):
        confirm_return_received(session, event_id=ev.event_id, received_date=D18, actor_user_id=staff_user.user_id)


def test_filters_and_manual_rejected_return(session, staff_user, production_manager, supplier):
    uid = staff_user.user_id
    e1 = _offspec_return(session, uid, supplier)  # batch 1
    _recv(session, uid, supplier, net=D("10"), batch_number="030224-260619-00")
    b2 = session.query(Batch).filter_by(batch_number="030224-260619-00").one()
    mark_batch_rejected(session, batch_id=b2.batch_id, actor_user_id=uid, reason="MD")
    e2 = return_to_supplier(session, batch_id=b2.batch_id, actor_user_id=production_manager.user_id,
                            reason="MD logam", event_date=D18)
    confirm_return_received(session, event_id=e1.event_id, received_date=D18, actor_user_id=uid)
    assert {r.event_id for r in list_supplier_returns(session)} == {e1.event_id, e2.event_id}
    [sent] = list_supplier_returns(session, status="DIKIRIM")
    assert sent.event_id == e2.event_id and sent.source == "REJECTED_BATCH" and sent.reason == "MD logam"
    [got] = list_supplier_returns(session, status="DITERIMA")
    assert got.event_id == e1.event_id
    assert [r.event_id for r in list_supplier_returns(session, batch_id=b2.batch_id)] == [e2.event_id]
    assert len(list_supplier_returns(session, supplier_id=supplier.supplier_id)) == 2
    assert list_supplier_returns(session, supplier_id=12345) == []
    with pytest.raises(ValueError):
        list_supplier_returns(session, status="APA")




# ------------------------------------------------ (3) tanggal MULAI sortasi
def _sort(session, uid, batch_id, **kw):
    return record_sortation(session, SortationInput(
        pic_user_id=uid, batch_id=batch_id, eg_qty=D("30"), **kw))


def test_sortation_start_date_required(session, staff_user, supplier):
    _recv(session, staff_user.user_id, supplier)
    bid = session.query(Batch).one().batch_id
    with pytest.raises(ValueError, match="MULAI"):
        _sort(session, staff_user.user_id, bid, event_date=None)


def test_sortation_end_date_before_start_is_rejected(session, staff_user, supplier):
    _recv(session, staff_user.user_id, supplier)
    bid = session.query(Batch).one().batch_id
    with pytest.raises(ValueError, match="lebih awal dari tanggal MULAI"):
        _sort(session, staff_user.user_id, bid, event_date=dt.date(2026, 6, 20), end_date=dt.date(2026, 6, 19))
    assert session.query(ProcessEvent).filter_by(event_type=EventType.SORTATION).count() == 0
    _sort(session, staff_user.user_id, bid, event_date=dt.date(2026, 6, 19), end_date=dt.date(2026, 6, 20))
    assert session.query(ProcessEvent).filter_by(event_type=EventType.SORTATION).count() == 1
