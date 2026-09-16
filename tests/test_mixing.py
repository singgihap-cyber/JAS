import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.mixing import (
    MIXED_SUPPLIER_CODE,
    PROCESS_CODE_MIXING,
    MixingInput,
    MixingSource,
    record_mixing,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving

TODAY = dt.date(2026, 9, 16)


def _receive(session, staff_user, supplier, qty=Decimal("10.000")):
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


# --- MANY->ONE combine (module docstring #1/#2) -----------------------------


def test_mixing_combines_sources_and_derives_cp_and_shrinkage(session, staff_user, supplier):
    b1 = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("20.000"))
    b3 = _receive(session, staff_user, supplier, qty=Decimal("15.000"))

    event = record_mixing(
        session,
        MixingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[
                MixingSource(batch_id=b1, quantity=Decimal("10.000")),
                MixingSource(batch_id=b2, quantity=Decimal("20.000")),
                MixingSource(batch_id=b3, quantity=Decimal("15.000")),
            ],
            final_qty=Decimal("44.000"),
            product_description="GOURMET",
        ),
    )

    assert event.event_type == EventType.MIXING
    inputs = [l for l in event.links if l.role.value == "INPUT"]
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert {l.batch_id for l in inputs} == {b1, b2, b3}
    assert len(outputs) == 1

    # cp_qty = 10+20+15 = 45; shrinkage = 45 - 44 = 1 (module docstring #1/#2)
    assert event.shrinkage_qty == Decimal("1.000")
    assert outputs[0].quantity == Decimal("44.000")

    for bid in (b1, b2, b3):
        assert session.get(Batch, bid).status == BatchStatus.CONSUMED

    new_batch = session.get(Batch, outputs[0].batch_id)
    assert new_batch.current_quantity == Decimal("44.000")
    assert new_batch.status == BatchStatus.ACTIVE

    notes = json.loads(event.notes)
    assert notes["product_description"] == "GOURMET"
    assert notes["cp_qty"] == "45.000"


def test_mixing_backward_trace_keeps_all_parents(session, staff_user, supplier):
    """GENEALOGY.md §7.1 -- field-mapping layer must not lose a parent batch."""
    from traceability_engine.services.genealogy import backward_trace

    b1 = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("20.000"))

    event = record_mixing(
        session,
        MixingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[
                MixingSource(batch_id=b1, quantity=Decimal("10.000")),
                MixingSource(batch_id=b2, quantity=Decimal("20.000")),
            ],
            final_qty=Decimal("30.000"),
            product_description="EG",
        ),
    )
    mixed = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    node = backward_trace(session, mixed)
    assert {p.batch.batch_id for p in node.parents} == {b1, b2}


# --- Combined batch defaults (module docstring #4/#5/#6/#7/#8) --------------


def test_mixing_output_defaults(session, staff_user, supplier):
    b1 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))

    event = record_mixing(
        session,
        MixingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[
                MixingSource(batch_id=b1, quantity=Decimal("5.000")),
                MixingSource(batch_id=b2, quantity=Decimal("5.000")),
            ],
            final_qty=Decimal("10.000"),
            product_description="GOURMET",
        ),
    )
    new_batch_id = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    new_batch = session.get(Batch, new_batch_id)

    assert new_batch.batch_type == BatchType.PROCESSED
    assert new_batch.process_code == PROCESS_CODE_MIXING
    assert new_batch.process_code == "03"
    assert new_batch.supplier_id is None
    assert new_batch.supplier_code == MIXED_SUPPLIER_CODE
    assert new_batch.jenis_code is None
    assert new_batch.grade_code is None


def test_mixing_accepts_explicit_overrides(session, staff_user, supplier):
    b1 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))

    event = record_mixing(
        session,
        MixingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[
                MixingSource(batch_id=b1, quantity=Decimal("5.000")),
                MixingSource(batch_id=b2, quantity=Decimal("5.000")),
            ],
            final_qty=Decimal("10.000"),
            product_description="GOURMET",
            grade_code="01",
            jenis_code="03",
            supplier_id=supplier.supplier_id,
            supplier_code=supplier.supplier_code,
        ),
    )
    new_batch_id = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    new_batch = session.get(Batch, new_batch_id)
    assert new_batch.grade_code == "01"
    assert new_batch.jenis_code == "03"
    assert new_batch.supplier_id == supplier.supplier_id
    assert new_batch.supplier_code == supplier.supplier_code


# --- Validation (module docstring #9/#10) -----------------------------------


def test_mixing_requires_at_least_two_sources(session, staff_user, supplier):
    b1 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    with pytest.raises(ValueError):
        record_mixing(
            session,
            MixingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[MixingSource(batch_id=b1, quantity=Decimal("5.000"))],
                final_qty=Decimal("5.000"),
                product_description="GOURMET",
            ),
        )


def test_mixing_rejects_duplicate_source_batch(session, staff_user, supplier):
    b1 = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(ValueError):
        record_mixing(
            session,
            MixingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[
                    MixingSource(batch_id=b1, quantity=Decimal("5.000")),
                    MixingSource(batch_id=b1, quantity=Decimal("5.000")),
                ],
                final_qty=Decimal("10.000"),
                product_description="GOURMET",
            ),
        )


def test_mixing_rejects_non_positive_source_quantity(session, staff_user, supplier):
    b1 = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("10.000"))
    with pytest.raises(ValueError):
        record_mixing(
            session,
            MixingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[
                    MixingSource(batch_id=b1, quantity=Decimal("0")),
                    MixingSource(batch_id=b2, quantity=Decimal("10.000")),
                ],
                final_qty=Decimal("10.000"),
                product_description="GOURMET",
            ),
        )


def test_mixing_rejects_non_positive_final_qty(session, staff_user, supplier):
    b1 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    with pytest.raises(ValueError):
        record_mixing(
            session,
            MixingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                sources=[
                    MixingSource(batch_id=b1, quantity=Decimal("5.000")),
                    MixingSource(batch_id=b2, quantity=Decimal("5.000")),
                ],
                final_qty=Decimal("0"),
                product_description="GOURMET",
            ),
        )


def test_mixing_negative_shrinkage_not_blocked(session, staff_user, supplier):
    """Consistent with Sundrying/Sortation: no shrinkage-tolerance rule is
    invented (PROCESS_RULES.md, still [UNCONFIRMED]) -- module docstring #2."""
    b1 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    b2 = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    event = record_mixing(
        session,
        MixingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[
                MixingSource(batch_id=b1, quantity=Decimal("5.000")),
                MixingSource(batch_id=b2, quantity=Decimal("5.000")),
            ],
            final_qty=Decimal("11.000"),  # > cp_qty (10.000) -- implausible but not blocked
            product_description="GOURMET",
        ),
    )
    assert event.shrinkage_qty == Decimal("-1.000")
