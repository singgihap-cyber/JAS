import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
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
    assert batch.current_quantity == Decimal("55.500")
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
