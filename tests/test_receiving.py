import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch, ProcessEvent
from traceability_engine.services.receiving import ReceivingInput, record_receiving

TODAY = dt.date(2026, 9, 14)


def test_receiving_with_manual_batch_number_parses_components(session, staff_user, supplier):
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=dt.date(2026, 2, 21),
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_KERING,
            net_quantity=Decimal("55.500"),
            batch_number="030224-260221-00",  # Proses.docx worked example
            product_description="GOURMET",
            packaging_condition="Karung baik",
            coly=5,
            gross_weight=Decimal("58.000"),
            tare_weight=Decimal("2.500"),
            on_spec_qty=Decimal("50.000"),
            off_spec_qty=Decimal("5.500"),
            smell_test="Normal",
        ),
    )
    assert event.event_type == EventType.RECEIVING
    batch = session.query(Batch).filter_by(created_from_event_id=event.event_id).one()
    # Fase 29: off-spec 5.500 otomatis dikembalikan -> stok = 55.500 - 5.500
    assert batch.current_quantity == Decimal("50.000")
    assert batch.status == BatchStatus.ACTIVE
    assert batch.batch_number == "030224-260221-00"
    assert batch.jenis_code == "03"
    assert batch.grade_code == "02"
    assert batch.supplier_code == "24"
    assert batch.receiving_date == dt.date(2026, 2, 21)

    notes = json.loads(event.notes)
    assert notes["on_spec_qty"] == "50.000"
    assert notes["off_spec_qty"] == "5.500"
    assert notes["smell_test"] == "Normal"


def test_receiving_without_batch_number_leaves_it_null(session, staff_user, supplier):
    """The generator is intentionally unimplemented (AA segment blocker) --
    Receiving must still work with batch_number left null."""
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_HIJAU,
            net_quantity=Decimal("20.000"),
        ),
    )
    batch = session.query(Batch).filter_by(created_from_event_id=event.event_id).one()
    assert batch.batch_number is None
    assert batch.batch_type == BatchType.RAW_HIJAU
    assert batch.current_quantity == Decimal("20.000")


def test_receiving_accepts_unparseable_manual_batch_number(session, staff_user, supplier):
    """A staff-typed batch number that doesn't match either known width is
    still accepted as free-form manual input, not rejected."""
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_KERING,
            net_quantity=Decimal("10.000"),
            batch_number="TEMP-BELUM-FINAL",
        ),
    )
    batch = session.query(Batch).filter_by(created_from_event_id=event.event_id).one()
    assert batch.batch_number == "TEMP-BELUM-FINAL"
    assert batch.jenis_code is None  # not parsed, but not rejected either


def test_receiving_rejects_non_positive_net_quantity(session, staff_user, supplier):
    with pytest.raises(ValueError):
        record_receiving(
            session,
            ReceivingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                supplier_id=supplier.supplier_id,
                batch_type=BatchType.RAW_KERING,
                net_quantity=Decimal("0"),
            ),
        )


def test_off_spec_is_auto_returned_to_supplier(session, staff_user, supplier):
    """Fase 29 -- off_spec_qty > 0 => event SUPPLIER_RETURN otomatis; RECEIVING
    tetap netto penuh, batch tidak REJECTED, ledger konsisten."""
    from traceability_engine.models import StockTransaction
    from traceability_engine.services.stock import reconcile_batch

    ev = record_receiving(session, ReceivingInput(
        event_date=TODAY, pic_user_id=staff_user.user_id, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_KERING, net_quantity=Decimal("20"), off_spec_qty=Decimal("3.5")))
    batch = session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()
    assert batch.current_quantity == Decimal("16.5") and batch.status == BatchStatus.ACTIVE
    ret = session.query(ProcessEvent).filter_by(event_type=EventType.SUPPLIER_RETURN).one()
    notes = json.loads(ret.notes)
    assert notes["source"] == "RECEIVING_OFF_SPEC" and notes["receiving_event_id"] == ev.event_id
    txs = session.query(StockTransaction).filter_by(batch_id=batch.batch_id).order_by(StockTransaction.transaction_id).all()
    assert [(t.direction.value, t.quantity) for t in txs] == [("IN", Decimal("20")), ("OUT", Decimal("3.5"))]
    reconcile_batch(session, batch.batch_id)


def test_no_return_event_when_off_spec_absent_or_zero(session, staff_user, supplier):
    for off in (None, Decimal("0")):
        record_receiving(session, ReceivingInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_KERING, net_quantity=Decimal("10"), off_spec_qty=off))
    assert session.query(ProcessEvent).filter_by(event_type=EventType.SUPPLIER_RETURN).count() == 0


def test_off_spec_cannot_exceed_net_or_be_negative(session, staff_user, supplier):
    for off in (Decimal("10.001"), Decimal("-1")):
        with pytest.raises(ValueError):
            record_receiving(session, ReceivingInput(
                event_date=TODAY, pic_user_id=staff_user.user_id, supplier_id=supplier.supplier_id,
                batch_type=BatchType.RAW_KERING, net_quantity=Decimal("10"), off_spec_qty=off))
