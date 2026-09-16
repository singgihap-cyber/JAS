import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.vacuum_packing import (
    PackingInput,
    PackingSource,
    VacuumInput,
    VacuumPlasticLine,
    record_packing,
    record_vacuum,
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


# --- Vacuum: self-loop, no new batch (module docstring #1) ------------------


def test_vacuum_is_self_loop_and_stock_neutral(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("50.000"))

    event = record_vacuum(
        session,
        VacuumInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            product_description="GOURMET",
            buyer="MALIK SABYTAEV",
            plastic_lines=[
                VacuumPlasticLine(
                    plastic_size="25 X 37,5",
                    plastic_lot="PV-2537-240904",
                    plastic_qty=Decimal("20"),
                    weight_per_pack=Decimal("1"),
                    total_weight=Decimal("20.000"),
                ),
                VacuumPlasticLine(
                    plastic_size="15 X 25",
                    plastic_lot="PV-1525-230902",
                    plastic_qty=Decimal("1"),
                    weight_per_pack=Decimal("0.03"),
                    total_weight=Decimal("0.030"),
                ),
            ],
        ),
    )

    assert event.event_type == EventType.VACUUM
    inputs = [l for l in event.links if l.role.value == "INPUT"]
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(inputs) == 1 and len(outputs) == 1
    assert inputs[0].batch_id == outputs[0].batch_id == batch_id
    # module docstring #2: quantity = SUM(plastic line total_weight)
    assert inputs[0].quantity == Decimal("20.030")
    assert outputs[0].quantity == Decimal("20.030")

    batch = session.get(Batch, batch_id)
    assert batch.status == BatchStatus.ACTIVE
    assert batch.current_quantity == Decimal("50.000")  # net-zero self-loop

    notes = json.loads(event.notes)
    assert notes["product_description"] == "GOURMET"
    assert notes["buyer"] == "MALIK SABYTAEV"
    assert len(notes["plastic_lines"]) == 2
    assert notes["plastic_lines"][0]["plastic_lot"] == "PV-2537-240904"


def test_vacuum_requires_at_least_one_plastic_line(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_vacuum(
            session,
            VacuumInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                plastic_lines=[],
            ),
        )


def test_vacuum_rejects_non_positive_line_weight(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_vacuum(
            session,
            VacuumInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                plastic_lines=[VacuumPlasticLine(total_weight=Decimal("0"))],
            ),
        )


def test_vacuum_insufficient_stock_raises(session, staff_user, supplier):
    from traceability_engine.exceptions import InsufficientStockError

    batch_id = _receive(session, staff_user, supplier, qty=Decimal("1.000"))
    with pytest.raises(InsufficientStockError):
        record_vacuum(
            session,
            VacuumInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                plastic_lines=[VacuumPlasticLine(total_weight=Decimal("5.000"))],
            ),
        )


# --- Packing: single-source ONE->ONE PackagingLot (module docstring #12) ----


def test_packing_single_source_inherits_and_derives_tare(session, staff_user, supplier):
    source_id = _receive(session, staff_user, supplier, qty=Decimal("1000.000"))
    source = session.get(Batch, source_id)
    source.grade_code = "03"
    source.jenis_code = "03"
    session.flush()

    event = record_packing(
        session,
        PackingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[PackingSource(batch_id=source_id, quantity=Decimal("1000.000"))],
            gross_weight=Decimal("1057.200"),
            plastic_size="40X70",
            plastic_lot="PV 4070-251213",
            plastic_qty=Decimal("200"),
            carton_lot="KEPMCC-250916",
            carton_qty=Decimal("40"),
            shipping_number="LN/EP/260408-007",
            destination="USA",
            product_description="EP",
            buyer="MCC",
        ),
    )

    assert event.event_type == EventType.PACKING
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(outputs) == 1
    # module docstring #6: net_weight derived = SUM(sources)
    assert outputs[0].quantity == Decimal("1000.000")

    packed = session.get(Batch, outputs[0].batch_id)
    assert packed.batch_type == BatchType.PACKAGED
    assert packed.net_weight == Decimal("1000.000")
    # module docstring #7: tare = gross - net = 1057.2 - 1000 = 57.2
    assert packed.tare_weight == Decimal("57.200")
    assert packed.gross_weight == Decimal("1057.200")
    assert packed.plastic_size == "40X70"
    assert packed.carton_lot == "KEPMCC-250916"
    # module docstring #12: single-source inheritance
    assert packed.grade_code == "03"
    assert packed.jenis_code == "03"
    assert packed.supplier_id == supplier.supplier_id

    source_after = session.get(Batch, source_id)
    assert source_after.status == BatchStatus.CONSUMED
    assert source_after.current_quantity == Decimal("0.000")

    notes = json.loads(event.notes)
    assert notes["shipping_number"] == "LN/EP/260408-007"
    assert notes["destination"] == "USA"
    assert notes["carton_qty"] == "40"


def test_packing_multi_source_does_not_auto_inherit(session, staff_user, supplier):
    """Confirmed real (GENEALOGY.md §3.1 / Fase 3
    test_packing_can_combine_multiple_batches): MANY->ONE Packing. Module
    docstring #12: grade/jenis/supplier are NOT inherited when there is more
    than one source."""
    p1 = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    p2 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    session.get(Batch, p1).grade_code = "01"
    session.get(Batch, p2).grade_code = "02"
    session.flush()

    event = record_packing(
        session,
        PackingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[
                PackingSource(batch_id=p1, quantity=Decimal("10.000")),
                PackingSource(batch_id=p2, quantity=Decimal("5.000")),
            ],
            gross_weight=Decimal("16.500"),
        ),
    )

    inputs = [l for l in event.links if l.role.value == "INPUT"]
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(inputs) == 2
    assert outputs[0].quantity == Decimal("15.000")

    packed = session.get(Batch, outputs[0].batch_id)
    assert packed.grade_code is None
    assert packed.jenis_code is None
    assert packed.tare_weight == Decimal("1.500")


def test_packing_requires_at_least_one_source(session, staff_user, supplier):
    with pytest.raises(ValueError):
        record_packing(
            session,
            PackingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[],
                gross_weight=Decimal("10.000"),
            ),
        )


def test_packing_rejects_duplicate_source_batch(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(ValueError):
        record_packing(
            session,
            PackingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[
                    PackingSource(batch_id=batch_id, quantity=Decimal("5.000")),
                    PackingSource(batch_id=batch_id, quantity=Decimal("5.000")),
                ],
                gross_weight=Decimal("10.500"),
            ),
        )


def test_packing_rejects_non_positive_gross_weight(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(ValueError):
        record_packing(
            session,
            PackingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[PackingSource(batch_id=batch_id, quantity=Decimal("10.000"))],
                gross_weight=Decimal("0"),
            ),
        )


def test_packing_process_code_defaults_to_none_unconfirmed(session, staff_user, supplier):
    """Module docstring #13: no confirmed PP code for Packing yet."""
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    event = record_packing(
        session,
        PackingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[PackingSource(batch_id=batch_id, quantity=Decimal("10.000"))],
            gross_weight=Decimal("10.500"),
        ),
    )
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    packed = session.get(Batch, outputs[0].batch_id)
    assert packed.process_code is None
