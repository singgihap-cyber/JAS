import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.rework import (
    GRADE_EG,
    GRADE_EP,
    GRADE_GOURMET,
    GRADE_NC,
    PROCESS_CODE_REWORK,
    ReworkInput,
    record_rework,
)

TODAY = dt.date(2026, 9, 14)


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


# --- ONE->MANY split (GENEALOGY.md §3.1, module docstring #1) ---------------


def test_rework_splits_into_grade_breakdown(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("50.000"))

    event = record_rework(
        session,
        ReworkInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            process_description="Re-sort reject lot",
            gourmet_qty=Decimal("20.000"),
            eg_qty=Decimal("15.000"),
            ep_qty=Decimal("8.000"),
            nc_qty=Decimal("5.000"),
        ),
    )

    assert event.event_type == EventType.REWORK
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(outputs) == 4
    assert {o.quantity for o in outputs} == {
        Decimal("20.000"),
        Decimal("15.000"),
        Decimal("8.000"),
        Decimal("5.000"),
    }
    # 50 - (20+15+8+5) = 2.000 shrinkage, derived (module docstring #7)
    assert event.shrinkage_qty == Decimal("2.000")

    source = session.get(Batch, batch_id)
    assert source.status == BatchStatus.CONSUMED
    assert source.current_quantity == Decimal("0.000")

    new_batches = {session.get(Batch, l.batch_id): l.quantity for l in outputs}
    grade_by_qty = {qty: b.grade_code for b, qty in new_batches.items()}
    assert grade_by_qty[Decimal("20.000")] == GRADE_GOURMET
    assert grade_by_qty[Decimal("15.000")] == GRADE_EG
    assert grade_by_qty[Decimal("8.000")] == GRADE_EP
    assert grade_by_qty[Decimal("5.000")] == GRADE_NC

    for b in new_batches:
        assert b.status == BatchStatus.ACTIVE
        assert b.supplier_id == supplier.supplier_id
        assert b.batch_type == BatchType.PROCESSED
        assert b.process_code == PROCESS_CODE_REWORK  # hardcoded "04" -- see #5

    notes = json.loads(event.notes)
    assert notes["process_description"] == "Re-sort reject lot"


def test_rework_single_grade_is_one_to_new_batch(session, staff_user, supplier):
    """Passing exactly one grade field naturally produces the
    ONE->NEW-BATCH shape already tested in Fase 3
    (`test_events.py::test_rework_creates_new_batch`)."""
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("8.000"))

    event = record_rework(
        session,
        ReworkInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            gourmet_qty=Decimal("8.000"),
        ),
    )
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(outputs) == 1
    assert event.shrinkage_qty == Decimal("0.000")
    new_batch = session.get(Batch, outputs[0].batch_id)
    assert new_batch.grade_code == GRADE_GOURMET
    assert new_batch.process_code == PROCESS_CODE_REWORK


def test_rework_has_no_powder_slot(session, staff_user, supplier):
    """Confirmed real difference from Sortation -- module docstring #1: the
    REW form header has no Powder breakdown column, so ReworkInput must not
    accept a powder_qty field at all."""
    assert not hasattr(ReworkInput, "powder_qty")
    with pytest.raises(TypeError):
        ReworkInput(
            event_date=TODAY,
            pic_user_id=1,
            batch_id=1,
            powder_qty=Decimal("1.000"),
        )


# --- Defaults / validation ---------------------------------------------------


def test_rework_defaults_starting_qty_to_batch_on_hand(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_rework(
        session,
        ReworkInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            gourmet_qty=Decimal("42.000"),
        ),
    )
    inputs = [l for l in event.links if l.role.value == "INPUT"]
    assert inputs[0].quantity == Decimal("42.000")


def test_rework_rejects_non_positive_starting_qty(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_rework(
            session,
            ReworkInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                starting_qty=Decimal("0"),
                gourmet_qty=Decimal("10.000"),
            ),
        )


def test_rework_rejects_negative_grade_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_rework(
            session,
            ReworkInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                gourmet_qty=Decimal("-1.000"),
            ),
        )


def test_rework_requires_at_least_one_grade_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_rework(
            session,
            ReworkInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
            ),
        )


# --- Inheritance from source batch (module docstring #4) --------------------


def test_rework_output_batches_inherit_supplier_and_receiving_date(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("30.000"))
    source = session.get(Batch, batch_id)
    source.jenis_code = "03"
    session.flush()

    event = record_rework(
        session,
        ReworkInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            gourmet_qty=Decimal("30.000"),
        ),
    )
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    new_batch = session.get(Batch, outputs[0].batch_id)
    assert new_batch.supplier_id == supplier.supplier_id
    assert new_batch.jenis_code == "03"
    assert new_batch.receiving_date == source.receiving_date


def test_rework_batch_type_override(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("10.000"))

    event = record_rework(
        session,
        ReworkInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            gourmet_qty=Decimal("10.000"),
            gourmet_batch_type=BatchType.RAW_KERING,
        ),
    )
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    new_batch = session.get(Batch, outputs[0].batch_id)
    assert new_batch.batch_type == BatchType.RAW_KERING
