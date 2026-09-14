import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType, TransactionDirection
from traceability_engine.exceptions import (
    InsufficientStockError,
    InvalidEventStructureError,
    QuantityReconciliationError,
)
from traceability_engine.models import Batch, StockTransaction
from traceability_engine.services.events import InputSpec, NewBatchSpec, OutputSpec, record_process_event

TODAY = dt.date(2026, 9, 14)


# --- TEST_CASES.md #1: Receiving creates batch + stock IN -----------------

def test_receiving_creates_batch_and_stock_in(session, staff_user, supplier):
    event = record_process_event(
        session,
        event_type=EventType.RECEIVING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[],
        outputs=[
            OutputSpec(
                quantity=Decimal("100.000"),
                new_batch=NewBatchSpec(
                    batch_type=BatchType.RAW_KERING,
                    supplier_id=supplier.supplier_id,
                    unit="kg",
                ),
            )
        ],
    )
    batch = session.query(Batch).one()
    assert batch.current_quantity == Decimal("100.000")
    assert batch.status == BatchStatus.ACTIVE
    assert batch.created_from_event_id == event.event_id

    txns = session.query(StockTransaction).all()
    assert len(txns) == 1
    assert txns[0].direction == TransactionDirection.IN
    assert txns[0].quantity == Decimal("100.000")


def test_receiving_rejects_inputs(session, staff_user, supplier):
    with pytest.raises(InvalidEventStructureError):
        record_process_event(
            session,
            event_type=EventType.RECEIVING,
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            inputs=[InputSpec(batch_id=1, quantity=Decimal("1"))],
            outputs=[
                OutputSpec(
                    quantity=Decimal("1"),
                    new_batch=NewBatchSpec(batch_type=BatchType.RAW_KERING),
                )
            ],
        )


# --- TEST_CASES.md #2: ONE->ONE processing preserves genealogy ------------

def _receive(session, staff_user, supplier, qty=Decimal("100.000")):
    event = record_process_event(
        session,
        event_type=EventType.RECEIVING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[],
        outputs=[
            OutputSpec(
                quantity=qty,
                new_batch=NewBatchSpec(batch_type=BatchType.RAW_KERING, supplier_id=supplier.supplier_id),
            )
        ],
    )
    batch_id = [l.batch_id for l in event.links][0]
    return batch_id


def test_one_to_one_inspection_is_self_loop_and_stock_neutral(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)

    qc_event = record_process_event(
        session,
        event_type=EventType.QC_TEST,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=batch_id, quantity=Decimal("100.000"))],
        outputs=[OutputSpec(batch_id=batch_id, quantity=Decimal("100.000"))],
        notes="KA/AW pengujian awal",
    )
    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("100.000")  # net-neutral
    assert batch.status == BatchStatus.ACTIVE
    assert {l.batch_id for l in qc_event.links} == {batch_id}
    assert len(qc_event.links) == 2  # one INPUT row + one OUTPUT row, same batch


def test_one_to_one_with_shrinkage_reduces_stock(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)

    record_process_event(
        session,
        event_type=EventType.SUNDRYING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=batch_id, quantity=Decimal("100.000"))],
        outputs=[OutputSpec(batch_id=batch_id, quantity=Decimal("70.000"))],
        shrinkage_qty=Decimal("30.000"),
    )
    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("70.000")


# --- TEST_CASES.md #3: Sortation splits one batch into multiple outputs ---

def test_sortation_splits_into_multiple_outputs(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("100.000"))

    event = record_process_event(
        session,
        event_type=EventType.SORTATION,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=batch_id, quantity=Decimal("100.000"))],
        outputs=[
            OutputSpec(quantity=Decimal("60.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
            OutputSpec(quantity=Decimal("30.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
        ],
        shrinkage_qty=Decimal("10.000"),
    )
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(outputs) == 2
    assert {o.quantity for o in outputs} == {Decimal("60.000"), Decimal("30.000")}

    source = session.get(Batch, batch_id)
    assert source.status == BatchStatus.CONSUMED
    assert source.current_quantity == Decimal("0.000")


def test_sortation_downgrade_is_single_output_reclassified_manually(session, staff_user, supplier):
    """Downgrade/Upgrade = a SORTATION event with exactly 1 output
    (GENEALOGY.md §3.1) -- not a separate event type."""
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("50.000"))

    event = record_process_event(
        session,
        event_type=EventType.SORTATION,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=batch_id, quantity=Decimal("50.000"))],
        outputs=[
            OutputSpec(quantity=Decimal("50.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED, process_code="02"))
        ],
    )
    assert event.event_type == EventType.SORTATION
    new_batch_id = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    assert session.get(Batch, new_batch_id).process_code == "02"  # Downgrade


# --- TEST_CASES.md #4: Mixing combines multiple source batches ------------

def test_mixing_combines_multiple_source_batches(session, staff_user, supplier):
    """Uses the GENEALOGY.md §7.1 worked example shape: 3 inputs -> 1 output."""
    b1 = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("20.000"))
    b3 = _receive(session, staff_user, supplier, qty=Decimal("15.000"))

    event = record_process_event(
        session,
        event_type=EventType.MIXING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[
            InputSpec(batch_id=b1, quantity=Decimal("10.000")),
            InputSpec(batch_id=b2, quantity=Decimal("20.000")),
            InputSpec(batch_id=b3, quantity=Decimal("15.000")),
        ],
        outputs=[
            OutputSpec(
                quantity=Decimal("45.000"),
                new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED, process_code="03", supplier_id=None),
            )
        ],
    )
    inputs = [l for l in event.links if l.role.value == "INPUT"]
    assert {l.batch_id for l in inputs} == {b1, b2, b3}
    for bid in (b1, b2, b3):
        assert session.get(Batch, bid).status == BatchStatus.CONSUMED


# --- TEST_CASES.md #5: Grinding creates a new powder batch ----------------

def test_grinding_creates_new_powder_batch(session, staff_user, supplier):
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("5.000"))

    event = record_process_event(
        session,
        event_type=EventType.GRINDING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=nc_batch, quantity=Decimal("5.000"))],
        outputs=[OutputSpec(quantity=Decimal("4.500"), new_batch=NewBatchSpec(batch_type=BatchType.POWDER))],
        shrinkage_qty=Decimal("0.500"),
    )
    powder_batch_id = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    powder = session.get(Batch, powder_batch_id)
    assert powder.batch_type == BatchType.POWDER
    assert powder.current_quantity == Decimal("4.500")


# --- TEST_CASES.md #6: Rework creates a new batch (incl. multi-output) ----

def test_rework_creates_new_batch(session, staff_user, supplier):
    source = _receive(session, staff_user, supplier, qty=Decimal("8.000"))
    event = record_process_event(
        session,
        event_type=EventType.REWORK,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=source, quantity=Decimal("8.000"))],
        outputs=[OutputSpec(quantity=Decimal("8.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED, process_code="04"))],
    )
    assert len([l for l in event.links if l.role.value == "OUTPUT"]) == 1


def test_rework_can_split_into_multiple_grade_outputs(session, staff_user, supplier):
    """Confirmed 2026-09-14 (GENEALOGY.md §3.1): Rework can be ONE->MANY,
    the same shape as Sortation."""
    source = _receive(session, staff_user, supplier, qty=Decimal("8.000"))
    event = record_process_event(
        session,
        event_type=EventType.REWORK,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=source, quantity=Decimal("8.000"))],
        outputs=[
            OutputSpec(quantity=Decimal("5.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED, process_code="04")),
            OutputSpec(quantity=Decimal("3.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED, process_code="04")),
        ],
    )
    assert len([l for l in event.links if l.role.value == "OUTPUT"]) == 2


# --- TEST_CASES.md #7: Packing links product and packaging lots -----------

def test_packing_single_batch_creates_packaged_lot(session, staff_user, supplier):
    product = _receive(session, staff_user, supplier, qty=Decimal("12.000"))
    event = record_process_event(
        session,
        event_type=EventType.PACKING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=product, quantity=Decimal("12.000"))],
        outputs=[
            OutputSpec(
                quantity=Decimal("12.000"),
                new_batch=NewBatchSpec(
                    batch_type=BatchType.PACKAGED,
                    plastic_size="1kg",
                    plastic_lot="PL-01",
                    carton_lot="CTN-01",
                    net_weight=Decimal("12.000"),
                ),
            )
        ],
    )
    packed_id = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    packed = session.get(Batch, packed_id)
    assert packed.batch_type == BatchType.PACKAGED
    assert packed.carton_lot == "CTN-01"


def test_packing_can_combine_multiple_batches(session, staff_user, supplier):
    """Confirmed 2026-09-14 (GENEALOGY.md §3.1): multi-batch Packing is real."""
    p1 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    p2 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    event = record_process_event(
        session,
        event_type=EventType.PACKING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[
            InputSpec(batch_id=p1, quantity=Decimal("5.000")),
            InputSpec(batch_id=p2, quantity=Decimal("5.000")),
        ],
        outputs=[OutputSpec(quantity=Decimal("10.000"), new_batch=NewBatchSpec(batch_type=BatchType.PACKAGED))],
    )
    assert len([l for l in event.links if l.role.value == "INPUT"]) == 2


# --- TEST_CASES.md #8/#9: Delivery / Sample Delivery -----------------------

def test_delivery_creates_stock_out(session, staff_user, supplier):
    packed = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    event = record_process_event(
        session,
        event_type=EventType.DELIVERY,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=packed, quantity=Decimal("10.000"))],
        outputs=[],
    )
    batch = session.get(Batch, packed)
    assert batch.current_quantity == Decimal("0.000")
    assert batch.status == BatchStatus.CONSUMED  # terminal "shipped" handling left to caller/UI (Fase 12)
    txn = session.query(StockTransaction).filter_by(event_id=event.event_id).one()
    assert txn.direction == TransactionDirection.OUT
    assert txn.is_sample is False


def test_delivery_rejects_outputs(session, staff_user, supplier):
    packed = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(InvalidEventStructureError):
        record_process_event(
            session,
            event_type=EventType.DELIVERY,
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            inputs=[InputSpec(batch_id=packed, quantity=Decimal("10.000"))],
            outputs=[OutputSpec(quantity=Decimal("1"), new_batch=NewBatchSpec(batch_type=BatchType.PACKAGED))],
        )


def test_sample_delivery_marks_stock_out_as_sample(session, staff_user, supplier):
    packed = _receive(session, staff_user, supplier, qty=Decimal("2.000"))
    event = record_process_event(
        session,
        event_type=EventType.SAMPLE_DELIVERY,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=packed, quantity=Decimal("2.000"))],
        outputs=[],
    )
    txn = session.query(StockTransaction).filter_by(event_id=event.event_id).one()
    assert txn.is_sample is True


# --- TEST_CASES.md #12: Quantity reconciliation detects inconsistency -----

def test_reconciliation_rejects_unbalanced_event(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("100.000"))
    with pytest.raises(QuantityReconciliationError):
        record_process_event(
            session,
            event_type=EventType.SORTATION,
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            inputs=[InputSpec(batch_id=batch_id, quantity=Decimal("100.000"))],
            outputs=[
                OutputSpec(quantity=Decimal("60.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
                OutputSpec(quantity=Decimal("30.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
            ],
            shrinkage_qty=Decimal("0.000"),  # 60+30+0 = 90 != 100 -- missing 10
        )


def test_reconciliation_has_no_tolerance(session, staff_user, supplier):
    """Confirmed 2026-09-14: even a 0.001 mismatch is a hard block."""
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("100.000"))
    with pytest.raises(QuantityReconciliationError):
        record_process_event(
            session,
            event_type=EventType.SUNDRYING,
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            inputs=[InputSpec(batch_id=batch_id, quantity=Decimal("100.000"))],
            outputs=[OutputSpec(batch_id=batch_id, quantity=Decimal("70.000"))],
            shrinkage_qty=Decimal("29.999"),  # off by 0.001
        )


def test_insufficient_stock_is_rejected(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(InsufficientStockError):
        record_process_event(
            session,
            event_type=EventType.SUNDRYING,
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            inputs=[InputSpec(batch_id=batch_id, quantity=Decimal("50.000"))],
            outputs=[OutputSpec(batch_id=batch_id, quantity=Decimal("50.000"))],
        )
