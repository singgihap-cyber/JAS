import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.curing import (
    AirdryingInput,
    BlanchingInput,
    CuringStageInput,
    StemRemovalInput,
    record_airdrying,
    record_blanching,
    record_first_curing,
    record_main_curing,
    record_second_curing,
    record_stem_removal,
    record_third_curing,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.steam_dry import SundryingInput, record_sundrying

TODAY = dt.date(2026, 9, 14)

# (event_type, record_fn) pairs -- Main/1st/2nd/3rd Curing share the exact
# same stock-neutral shape (curing.py module docstring #3, Fase 22).
STAGE_FNS = [
    (EventType.MAIN_CURING, record_main_curing),
    (EventType.FIRST_CURING, record_first_curing),
    (EventType.SECOND_CURING, record_second_curing),
    (EventType.THIRD_CURING, record_third_curing),
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


# --- Main/1st/2nd/3rd Curing: stock-neutral, parametrized ------------------


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_curing_stage_is_stock_neutral(session, staff_user, supplier, event_type, record_fn):
    """Fase 22 correction: PROSES HIJAU 2026.xlsx shows no ending-weight
    column for these four stages -- they never change stock, unlike the old
    Fase 19 Sundrying-shaped guess."""
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("100.000"))

    event = record_fn(
        session,
        CuringStageInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            duration_hours=Decimal("20"),
        ),
    )

    assert event.event_type == event_type
    assert {l.batch_id for l in event.links} == {batch_id}
    assert len(event.links) == 2  # self-loop: one INPUT + one OUTPUT
    assert event.shrinkage_qty == Decimal("0")

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("100.000")
    assert batch.status == BatchStatus.ACTIVE
    assert batch.batch_type == BatchType.RAW_HIJAU  # unchanged, self-loop

    notes = json.loads(event.notes)
    assert notes["duration_hours"] == "20"


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_curing_stage_defaults_quantity_to_batch_on_hand(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_fn(
        session,
        CuringStageInput(event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id),
    )
    assert event.shrinkage_qty == Decimal("0")
    assert session.get(Batch, batch_id).current_quantity == Decimal("42.000")
    assert event.notes is None  # no duration_hours supplied


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_curing_stage_accepts_explicit_partial_quantity(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("60.000"))

    record_fn(
        session,
        CuringStageInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            quantity=Decimal("20.000"),
        ),
    )
    # Stock-neutral self-loop -- the 20kg moved through and came straight
    # back, net balance unchanged.
    assert session.get(Batch, batch_id).current_quantity == Decimal("60.000")


@pytest.mark.parametrize("event_type,record_fn", STAGE_FNS)
def test_curing_stage_rejects_non_positive_quantity(session, staff_user, supplier, event_type, record_fn):
    batch_id = _receive_hijau(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_fn(
            session,
            CuringStageInput(
                event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
                quantity=Decimal("0"),
            ),
        )


# --- Lepas Tangkai (stem removal): shrinkage-based, new in Fase 22 --------


def test_stem_removal_reduces_stock_by_derived_limbah(session, staff_user, supplier):
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("77.740"))

    event = record_stem_removal(
        session,
        StemRemovalInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("75.440"),
        ),
    )

    assert event.event_type == EventType.STEM_REMOVAL
    assert len(event.links) == 2  # self-loop
    assert event.shrinkage_qty == Decimal("2.300")  # limbah, derived

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("75.440")
    assert batch.batch_type == BatchType.RAW_HIJAU  # unchanged, self-loop


def test_stem_removal_rejects_non_positive_final_quantity(session, staff_user, supplier):
    batch_id = _receive_hijau(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_stem_removal(
            session,
            StemRemovalInput(
                event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
                final_quantity=Decimal("0"),
            ),
        )


# --- Blanching: stock-neutral, its own event type (NOT Steaming) ----------


def test_blanching_is_stock_neutral_and_not_steaming(session, staff_user, supplier):
    """Tommy, 2026-09-19: 'Steaming hanya digunakan di proses kering' --
    Blanching must be EventType.BLANCHING, never EventType.STEAMING."""
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("75.440"))

    event = record_blanching(
        session,
        BlanchingInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            temperature=Decimal("65.0"), dip_duration_minutes=Decimal("2"),
        ),
    )

    assert event.event_type == EventType.BLANCHING
    assert event.event_type != EventType.STEAMING
    assert event.shrinkage_qty == Decimal("0")
    assert session.get(Batch, batch_id).current_quantity == Decimal("75.440")

    notes = json.loads(event.notes)
    assert notes["temperature"] == "65.0"
    assert notes["dip_duration_minutes"] == "2"


def test_blanching_rejects_non_positive_quantity(session, staff_user, supplier):
    batch_id = _receive_hijau(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_blanching(
            session,
            BlanchingInput(
                event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
                quantity=Decimal("0"),
            ),
        )


# --- Airdrying: keeps the shrinkage shape, gains duration_days/final_ka ---


def test_airdrying_reduces_stock_by_derived_shrinkage_and_records_ka(session, staff_user, supplier):
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("100.000"))

    event = record_airdrying(
        session,
        AirdryingInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("92.000"), duration_days=Decimal("19"),
            final_ka=Decimal("22.05"),
        ),
    )

    assert event.event_type == EventType.AIRDRYING
    assert event.shrinkage_qty == Decimal("8.000")
    assert session.get(Batch, batch_id).current_quantity == Decimal("92.000")

    notes = json.loads(event.notes)
    assert notes["duration_days"] == "19"
    assert notes["final_ka"] == "22.05"


def test_airdrying_defaults_starting_quantity_to_batch_on_hand(session, staff_user, supplier):
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_airdrying(
        session,
        AirdryingInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("40.000"),
        ),
    )
    assert event.shrinkage_qty == Decimal("2.000")
    assert session.get(Batch, batch_id).current_quantity == Decimal("40.000")
    assert event.notes is None  # no duration_days/final_ka supplied


def test_airdrying_rejects_non_positive_final_quantity(session, staff_user, supplier):
    batch_id = _receive_hijau(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_airdrying(
            session,
            AirdryingInput(
                event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
                final_quantity=Decimal("0"),
            ),
        )


# --- End-to-end Hijau route through the corrected curing chain ------------


def test_full_hijau_curing_chain_end_to_end(session, staff_user, supplier):
    """CLAUDE.md/WORKFLOW.md Hijau workflow, corrected in Fase 22:
    ... Lepas Tangkai -> Blanching -> Main Curing -> 1st -> 2nd -> 3rd
    Curing -> Sundrying -> Airdrying -> ...
    Lepas Tangkai and Airdrying reduce stock via derived shrinkage;
    Blanching and the four Curing stages are stock-neutral; Sundrying
    reuses the Fase 6 self-loop function unchanged."""
    batch_id = _receive_hijau(session, staff_user, supplier, qty=Decimal("100.000"))

    record_stem_removal(
        session,
        StemRemovalInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("97.000"),
        ),
    )
    assert session.get(Batch, batch_id).current_quantity == Decimal("97.000")

    record_blanching(
        session,
        BlanchingInput(event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id),
    )
    assert session.get(Batch, batch_id).current_quantity == Decimal("97.000")

    for record_fn in (record_main_curing, record_first_curing, record_second_curing, record_third_curing):
        record_fn(
            session,
            CuringStageInput(event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id),
        )
    assert session.get(Batch, batch_id).current_quantity == Decimal("97.000")

    # Sundrying -- reused from Fase 6.
    record_sundrying(
        session,
        SundryingInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("70.000"),
        ),
    )
    assert session.get(Batch, batch_id).current_quantity == Decimal("70.000")

    record_airdrying(
        session,
        AirdryingInput(
            event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id,
            final_quantity=Decimal("65.000"),
        ),
    )

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("65.000")
    assert batch.status == BatchStatus.ACTIVE
    assert batch.batch_type == BatchType.RAW_HIJAU
