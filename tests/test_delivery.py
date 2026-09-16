import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch, Shipment, StockTransaction
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.delivery import (
    DeliveryInput,
    DeliverySource,
    record_delivery,
    record_sample_delivery,
)

TODAY = dt.date(2026, 9, 16)


def _receive(session, staff_user, supplier, qty=Decimal("100.000")):
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_KERING,
            net_quantity=qty,
        ),
    )
    return session.query(Batch).filter_by(created_from_event_id=event.event_id).one().batch_id


# --- Delivery: single-source, full depletion -> SHIPPED (module docstring #8) --


def test_delivery_full_depletion_marks_batch_shipped(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("2.000"))

    event = record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("2.000"))],
            gross_weight=Decimal("2.275"),
            shipping_number="DN/G/260120-001",
            destination="Tangerang",
            recipient="LIBERTA GELATO",
            expedition="JNT",
            transport_condition="BAIK",
            packaging_condition="BAIK",
            coly=1,
        ),
    )

    assert event.event_type == EventType.DELIVERY
    inputs = [l for l in event.links if l.role.value == "INPUT"]
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(inputs) == 1
    assert outputs == []

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("0.000")
    assert batch.status == BatchStatus.SHIPPED  # not CONSUMED -- see #8

    txn = session.query(StockTransaction).filter_by(event_id=event.event_id).one()
    assert txn.direction.value == "OUT"
    assert txn.is_sample is False

    shipment = session.query(Shipment).filter_by(event_id=event.event_id).one()
    assert shipment.shipping_number == "DN/G/260120-001"
    assert shipment.destination == "Tangerang"
    assert shipment.recipient == "LIBERTA GELATO"
    # module docstring #3: net_weight = SUM(sources), tare = gross - net
    assert shipment.net_weight == Decimal("2.000")
    assert shipment.tare_weight == Decimal("0.275")
    assert shipment.gross_weight == Decimal("2.275")
    assert shipment.coly == 1
    assert shipment.customer_id is None


def test_delivery_partial_leaves_batch_active(session, staff_user, supplier):
    """A partial delivery must not promote the batch to SHIPPED -- only a
    fully-depleted batch is terminal (module docstring #8)."""
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))

    record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("4.000"))],
            gross_weight=Decimal("4.500"),
        ),
    )

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("6.000")
    assert batch.status == BatchStatus.ACTIVE


# --- Delivery: multi-batch under one shipment (module docstring #4) --------


def test_delivery_can_combine_multiple_source_batches(session, staff_user, supplier):
    """Confirmed real (PD sheet, shipping number LN/G/260517-013 spans two
    batches via a continuation row) -- module docstring #4."""
    b1 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("30.000"))

    event = record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[
                DeliverySource(batch_id=b1, quantity=Decimal("5.000")),
                DeliverySource(batch_id=b2, quantity=Decimal("30.000")),
            ],
            gross_weight=Decimal("36.600"),
            shipping_number="LN/G/260517-013",
            destination="Rusia",
            recipient="MALIK S/RUSIA",
        ),
    )

    inputs = [l for l in event.links if l.role.value == "INPUT"]
    assert len(inputs) == 2

    shipment = session.query(Shipment).filter_by(event_id=event.event_id).one()
    assert shipment.net_weight == Decimal("35.000")
    assert shipment.tare_weight == Decimal("1.600")

    assert session.get(Batch, b1).status == BatchStatus.SHIPPED
    assert session.get(Batch, b2).status == BatchStatus.SHIPPED


def test_delivery_requires_at_least_one_source(session, staff_user, supplier):
    with pytest.raises(ValueError):
        record_delivery(
            session,
            DeliveryInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[],
                gross_weight=Decimal("10.000"),
            ),
        )


def test_delivery_rejects_duplicate_source_batch(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(ValueError):
        record_delivery(
            session,
            DeliveryInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[
                    DeliverySource(batch_id=batch_id, quantity=Decimal("5.000")),
                    DeliverySource(batch_id=batch_id, quantity=Decimal("5.000")),
                ],
                gross_weight=Decimal("10.500"),
            ),
        )


def test_delivery_rejects_non_positive_gross_weight(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(ValueError):
        record_delivery(
            session,
            DeliveryInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("10.000"))],
                gross_weight=Decimal("0"),
            ),
        )


def test_delivery_rejects_non_positive_source_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(ValueError):
        record_delivery(
            session,
            DeliveryInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("0"))],
                gross_weight=Decimal("10.000"),
            ),
        )


# --- Sample Delivery: is_sample tagging + description notes (docstring #6/#10) --


def test_sample_delivery_marks_stock_out_as_sample_and_records_description(
    session, staff_user, supplier
):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("1.400"))

    event = record_sample_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("1.400"))],
            gross_weight=Decimal("1.505"),
            shipping_number="EP MCC 610893",
            destination="VIETNAM",
            recipient="SGS VIETNAM",
            description="EP",
        ),
    )

    assert event.event_type == EventType.SAMPLE_DELIVERY
    txn = session.query(StockTransaction).filter_by(event_id=event.event_id).one()
    assert txn.is_sample is True

    notes = json.loads(event.notes)
    assert notes["description"] == "EP"

    shipment = session.query(Shipment).filter_by(event_id=event.event_id).one()
    assert shipment.recipient == "SGS VIETNAM"
    assert shipment.net_weight == Decimal("1.400")
    assert shipment.tare_weight == Decimal("0.105")

    batch = session.get(Batch, batch_id)
    assert batch.status == BatchStatus.SHIPPED


def test_sample_delivery_without_description_has_no_notes(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("1.000"))
    event = record_sample_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("1.000"))],
            gross_weight=Decimal("1.100"),
        ),
    )
    assert event.notes is None


def test_delivery_insufficient_stock_raises(session, staff_user, supplier):
    from traceability_engine.exceptions import InsufficientStockError

    batch_id = _receive(session, staff_user, supplier, qty=Decimal("1.000"))
    with pytest.raises(InsufficientStockError):
        record_delivery(
            session,
            DeliveryInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("5.000"))],
                gross_weight=Decimal("5.000"),
            ),
        )


def test_delivery_customer_id_optional_and_stored(session, staff_user, supplier):
    """module docstring #7: customer_id is caller-supplied, no lookup here."""
    from traceability_engine.models import Customer

    customer = Customer(name="LIBERTA GELATO")
    session.add(customer)
    session.flush()

    batch_id = _receive(session, staff_user, supplier, qty=Decimal("2.000"))
    event = record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=batch_id, quantity=Decimal("2.000"))],
            gross_weight=Decimal("2.275"),
            customer_id=customer.customer_id,
            recipient="LIBERTA GELATO",
        ),
    )
    shipment = session.query(Shipment).filter_by(event_id=event.event_id).one()
    assert shipment.customer_id == customer.customer_id
