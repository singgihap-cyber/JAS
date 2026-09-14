import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import (
    GRADE_EG,
    GRADE_EP,
    GRADE_GOURMET,
    GRADE_NC,
    GRADE_POWDER,
    SortationInput,
    record_sortation,
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


# --- ONE->MANY split ---------------------------------------------------------


def test_sortation_splits_into_grade_breakdown(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("100.000"))

    event = record_sortation(
        session,
        SortationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            end_date=TODAY,
            gourmet_qty=Decimal("40.000"),
            eg_qty=Decimal("30.000"),
            ep_qty=Decimal("10.000"),
            nc_qty=Decimal("15.000"),
            powder_qty=Decimal("2.000"),
        ),
    )

    assert event.event_type == EventType.SORTATION
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(outputs) == 5
    assert {o.quantity for o in outputs} == {
        Decimal("40.000"),
        Decimal("30.000"),
        Decimal("10.000"),
        Decimal("15.000"),
        Decimal("2.000"),
    }
    # 100 - (40+30+10+15+2) = 3.000 shrinkage, derived (module docstring #6)
    assert event.shrinkage_qty == Decimal("3.000")

    source = session.get(Batch, batch_id)
    assert source.status == BatchStatus.CONSUMED
    assert source.current_quantity == Decimal("0.000")

    new_batches = {session.get(Batch, l.batch_id): l.quantity for l in outputs}
    grade_by_qty = {qty: b.grade_code for b, qty in new_batches.items()}
    assert grade_by_qty[Decimal("40.000")] == GRADE_GOURMET
    assert grade_by_qty[Decimal("30.000")] == GRADE_EG
    assert grade_by_qty[Decimal("10.000")] == GRADE_EP
    assert grade_by_qty[Decimal("15.000")] == GRADE_NC
    assert grade_by_qty[Decimal("2.000")] == GRADE_POWDER

    for b in new_batches:
        assert b.status == BatchStatus.ACTIVE
        assert b.supplier_id == supplier.supplier_id
        assert b.process_code == "00"
        if b.grade_code == GRADE_POWDER:
            assert b.batch_type == BatchType.POWDER
        else:
            assert b.batch_type == BatchType.PROCESSED

    notes = json.loads(event.notes)
    assert notes["end_date"] == TODAY.isoformat()


def test_sortation_omits_grades_left_unset(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("50.000"))

    event = record_sortation(
        session,
        SortationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            gourmet_qty=Decimal("50.000"),
        ),
    )
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(outputs) == 1
    assert event.shrinkage_qty == Decimal("0.000")


def test_sortation_defaults_initial_qty_to_batch_on_hand(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_sortation(
        session,
        SortationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            gourmet_qty=Decimal("42.000"),
        ),
    )
    inputs = [l for l in event.links if l.role.value == "INPUT"]
    assert inputs[0].quantity == Decimal("42.000")


def test_sortation_rejects_non_positive_initial_qty(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_sortation(
            session,
            SortationInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                initial_qty=Decimal("0"),
                gourmet_qty=Decimal("10.000"),
            ),
        )


def test_sortation_rejects_negative_grade_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_sortation(
            session,
            SortationInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                gourmet_qty=Decimal("-1.000"),
            ),
        )


def test_sortation_requires_at_least_one_grade_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_sortation(
            session,
            SortationInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
            ),
        )


# --- Downgrade/Upgrade: single-output re-grade (GENEALOGY.md §3.1) ----------


def test_sortation_downgrade_is_single_output_reclassified(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("50.000"))

    event = record_sortation(
        session,
        SortationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            eg_qty=Decimal("50.000"),  # re-graded down to EG
            process_code="02",  # Downgrade -- BATCH_NUMBER_SPEC.md PP codes
        ),
    )
    assert event.event_type == EventType.SORTATION
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert len(outputs) == 1
    new_batch = session.get(Batch, outputs[0].batch_id)
    assert new_batch.grade_code == GRADE_EG
    assert new_batch.process_code == "02"
    assert event.shrinkage_qty == Decimal("0.000")


def test_sortation_upgrade_is_single_output_reclassified(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("20.000"))

    event = record_sortation(
        session,
        SortationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            gourmet_qty=Decimal("20.000"),  # re-graded up to Gourmet
            process_code="01",  # Upgrade
        ),
    )
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    new_batch = session.get(Batch, outputs[0].batch_id)
    assert new_batch.grade_code == GRADE_GOURMET
    assert new_batch.process_code == "01"


# --- Inheritance from source batch (module docstring #4) --------------------


def test_sortation_output_batches_inherit_supplier_and_receiving_date(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("30.000"))
    source = session.get(Batch, batch_id)
    source.jenis_code = "03"
    session.flush()

    event = record_sortation(
        session,
        SortationInput(
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
