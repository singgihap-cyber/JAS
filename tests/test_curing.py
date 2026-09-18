import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.curing import (
    CuringStageInput,
    record_airdrying,
    record_first_curing,
    record_main_curing,
    record_second_curing,
    record_third_curing,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.steam_dry import (
    SteamingInput,
    SundryingInput,
    record_steaming,
    record_sundrying,
)

TODAY = dt.date(2026, 9, 14)

# (event_type, record_fn) pairs -- covers all five new stages with one
# parametrized suite instead of five near-identical copies.
STAGE_FNS = [
    (EventType.MAIN_CURING, record_main_curing),
    (EventType.FIRST_CURING, record_first_curing),
    (EventType.SECOND_CURING, record_second_curing),
    (EventType.THIRD_CURING, record_third_curing),
    (EventType.AIRDRYING, record_airdrying),
]


def _receive_hijau(session, staff_user, supplier, qty=Decimal("100.000")):
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_HIJAU,
            net_quantity=qty,
        ),
    )
    return session.query(Batch).filter_by(created_from_event_id=event.event_id).one().batch_id


# --- Each of the five stages, parametrized --------------------------------


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_stage_reduces_stock_by_derived_shrinkage(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier)

    event = record_fn(
        session,
        CuringStageInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            final_quantity=Decimal("92.000"),
            duration="3 hari",
            condition_notes="Dibungkus kain, disimpan hangat",
        ),
    )

    assert event.event_type == event_type
    assert {l.batch_id for l in event.links} == {batch_id}
    assert len(event.links) == 2  # self-loop: one INPUT + one OUTPUT
    assert event.shrinkage_qty == Decimal("8.000")  # 100 - 92, derived

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("92.000")
    assert batch.status == BatchStatus.ACTIVE
    assert batch.batch_type == BatchType.RAW_HIJAU  # unchanged, self-loop (#6)

    notes = json.loads(event.notes)
    assert notes["duration"] == "3 hari"
    assert notes["condition_notes"] == "Dibungkus kain, disimpan hangat"


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_stage_defaults_starting_quantity_to_batch_on_hand(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_fn(
        session,
        CuringStageInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            final_quantity=Decimal("40.000"),
        ),
    )
    assert event.shrinkage_qty == Decimal("2.000")
    assert session.get(Batch, batch_id).current_quantity == Decimal("40.000")
    assert event.notes is None  # no optional fields supplied


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_stage_zero_shrinkage_is_allowed(session, staff_user, supplier, event_type, record_fn):
    """A stage with no weight loss is a normal outcome, not an error --
    same as Steaming's stock-neutral self-loop (module docstring #3)."""
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("50.000"))

    event = record_fn(
        session,
        CuringStageInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            final_quantity=Decimal("50.000"),
        ),
    )
    assert event.shrinkage_qty == Decimal("0")
    assert session.get(Batch, batch_id).current_quantity == Decimal("50.000")


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_stage_accepts_explicit_partial_starting_quantity(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("60.000"))

    event = record_fn(
        session,
        CuringStageInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            starting_quantity=Decimal("20.000"),
            final_quantity=Decimal("15.000"),
        ),
    )
    assert event.shrinkage_qty == Decimal("5.000")
    # Only the partial amount moved through the self-loop -- remaining 40kg
    # untouched, net balance after is 60 - 20 (consumed) + 15 (returned) = 55.
    assert session.get(Batch, batch_id).current_quantity == Decimal("55.000")


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_stage_rejects_non_positive_final_quantity(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_fn(
            session,
            CuringStageInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                final_quantity=Decimal("0"),
            ),
        )


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_stage_rejects_non_positive_starting_quantity(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_fn(
            session,
            CuringStageInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                starting_quantity=Decimal("0"),
                final_quantity=Decimal("0"),
            ),
        )


# --- End-to-end Hijau route through the curing chain -----------------------


def test_full_hijau_curing_chain_end_to_end(session, staff_user, supplier):
    """CLAUDE.md Hijau workflow, the curing segment:
    ... Steaming/blanching -> Main Curing -> 1st -> 2nd -> 3rd Curing ->
    Sundrying -> Airdrying -> ...
    Steaming/blanching and Sundrying reuse the Fase 6 self-loop functions
    unchanged (module docstring #1); the five Fase 19 stages chain in
    between and each reduces stock by its own derived shrinkage."""
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("100.000"))

    # Steaming/blanching -- reused from Fase 6, stock-neutral.
    record_steaming(
        session,
        SteamingInput(event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id),
    )
    assert session.get(Batch, batch_id).current_quantity == Decimal("100.000")

    record_main_curing(
        session,
        CuringStageInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("95.000"),
        ),
    )
    record_first_curing(
        session,
        CuringStageInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("88.000"),
        ),
    )
    record_second_curing(
        session,
        CuringStageInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("82.000"),
        ),
    )
    record_third_curing(
        session,
        CuringStageInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("77.000"),
        ),
    )
    assert session.get(Batch, batch_id).current_quantity == Decimal("77.000")

    # Sundrying -- reused from Fase 6.
    record_sundrying(
        session,
        SundryingInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("50.000"),
        ),
    )
    assert session.get(Batch, batch_id).current_quantity == Decimal("50.000")

    record_airdrying(
        session,
        CuringStageInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("45.000"),
        ),
    )

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("45.000")
    assert batch.status == BatchStatus.ACTIVE
    assert batch.batch_type == BatchType.RAW_HIJAU
