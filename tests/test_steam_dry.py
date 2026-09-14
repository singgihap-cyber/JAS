import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.steam_dry import (
    SteamingInput,
    SundryingInput,
    record_steaming,
    record_sundrying,
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


# --- Steaming ---------------------------------------------------------------


def test_steaming_is_self_loop_and_stock_neutral(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)

    event = record_steaming(
        session,
        SteamingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            event_time=dt.time(8, 0),
            end_time=dt.time(8, 15),
            pan_count=4,
            water_condition="Baik",
            pan_condition="Baik",
            steam_temperature=Decimal("96.500"),
            verification_reading_1=Decimal("96.400"),
            verification_reading_2=Decimal("96.600"),
            verification_reading_3=Decimal("96.500"),
        ),
    )

    assert event.event_type == EventType.STEAMING
    assert {l.batch_id for l in event.links} == {batch_id}
    assert len(event.links) == 2  # one INPUT + one OUTPUT row, same batch (self-loop)
    assert event.shrinkage_qty == Decimal("0")

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("100.000")  # stock-neutral
    assert batch.status == BatchStatus.ACTIVE

    notes = json.loads(event.notes)
    assert notes["pan_count"] == 4
    assert notes["water_condition"] == "Baik"
    assert notes["pan_condition"] == "Baik"
    assert notes["steam_temperature"] == "96.500"
    assert notes["verification_readings"] == ["96.400", "96.600", "96.500"]
    assert notes["end_time"] == "08:15:00"


def test_steaming_defaults_quantity_to_batch_on_hand(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_steaming(
        session,
        SteamingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
        ),
    )
    quantities = {l.quantity for l in event.links}
    assert quantities == {Decimal("42.000")}
    assert event.notes is None  # no optional fields supplied


def test_steaming_accepts_explicit_partial_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("60.000"))

    event = record_steaming(
        session,
        SteamingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            quantity=Decimal("10.000"),
        ),
    )
    quantities = {l.quantity for l in event.links}
    assert quantities == {Decimal("10.000")}
    assert session.get(Batch, batch_id).current_quantity == Decimal("60.000")


def test_steaming_rejects_non_positive_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_steaming(
            session,
            SteamingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                quantity=Decimal("0"),
            ),
        )


# --- Sundrying ---------------------------------------------------------------


def test_sundrying_reduces_stock_by_derived_shrinkage(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)

    event = record_sundrying(
        session,
        SundryingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            final_quantity=Decimal("70.000"),
            starting_ka=Decimal("55.000"),
            drying_duration="60 menit x 3",
        ),
    )

    assert event.event_type == EventType.SUNDRYING
    assert {l.batch_id for l in event.links} == {batch_id}
    assert len(event.links) == 2  # self-loop
    assert event.shrinkage_qty == Decimal("30.000")  # 100 - 70, derived (#2)

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("70.000")
    assert batch.status == BatchStatus.ACTIVE

    notes = json.loads(event.notes)
    assert notes["starting_ka"] == "55.000"
    assert notes["drying_duration"] == "60 menit x 3"


def test_sundrying_defaults_starting_quantity_to_batch_on_hand(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_sundrying(
        session,
        SundryingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            final_quantity=Decimal("40.000"),
        ),
    )
    assert event.shrinkage_qty == Decimal("2.000")
    assert session.get(Batch, batch_id).current_quantity == Decimal("40.000")


def test_sundrying_accepts_explicit_partial_starting_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("60.000"))

    event = record_sundrying(
        session,
        SundryingInput(
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


def test_sundrying_rejects_non_positive_final_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_sundrying(
            session,
            SundryingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                final_quantity=Decimal("0"),
            ),
        )


def test_sundrying_rejects_non_positive_starting_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_sundrying(
            session,
            SundryingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                starting_quantity=Decimal("0"),
                final_quantity=Decimal("0"),
            ),
        )


def test_steaming_then_sundrying_end_to_end(session, staff_user, supplier):
    """CLAUDE.md Kering workflow: ... -> Steaming -> Sundrying -> ...
    Steaming is stock-neutral, Sundrying then applies the real shrinkage."""
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("100.000"))

    record_steaming(
        session,
        SteamingInput(event_date=TODAY, pic_user_id=staff_user.user_id, batch_id=batch_id),
    )
    assert session.get(Batch, batch_id).current_quantity == Decimal("100.000")

    record_sundrying(
        session,
        SundryingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            final_quantity=Decimal("62.000"),
        ),
    )
    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("62.000")
    assert batch.status == BatchStatus.ACTIVE
