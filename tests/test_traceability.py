"""Fase 14 -- Forward/Backward Traceability report layer
(`services/traceability.py`).

`services/genealogy.py`'s raw traversal (`backward_trace`/`forward_trace`)
already has its own tests (`tests/test_genealogy.py`, `TEST_CASES.md`
#10/#11). These tests cover the new report layer instead: `resolve_shipments`,
`incomplete_leaves`, `full_trace`, and `chain_of_custody_report` -- and, per
`14_TRACEABILITY.md`'s explicit wording ("across split/merge genealogy,
processes, packing and shipments"), one comprehensive integration scenario
that exercises every one of those shapes in a single chain, not just the
two-hop cases `test_genealogy.py` already covers.
"""
import datetime as dt
from decimal import Decimal

from traceability_engine.enums import BatchType, EventType, UserRole
from traceability_engine.models import Customer, Supplier, User
from traceability_engine.services.adjustment import mark_batch_rejected
from traceability_engine.services.delivery import DeliveryInput, DeliverySource, record_delivery
from traceability_engine.services.events import InputSpec, NewBatchSpec, OutputSpec, record_process_event
from traceability_engine.services.traceability import (
    chain_of_custody_report,
    full_trace,
    incomplete_leaves,
    resolve_shipments,
)

TODAY = dt.date(2026, 9, 17)
Q = Decimal


def _receive(session, staff_user, supplier, qty, batch_type=BatchType.RAW_KERING):
    event = record_process_event(
        session,
        event_type=EventType.RECEIVING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[],
        outputs=[
            OutputSpec(
                quantity=qty,
                new_batch=NewBatchSpec(batch_type=batch_type, supplier_id=supplier.supplier_id),
            )
        ],
    )
    return [l.batch_id for l in event.links][0]


def _output_ids(event):
    return [l.batch_id for l in event.links if l.role.value == "OUTPUT"]


def test_full_chain_split_merge_process_packing_shipment(session, staff_user, supplier):
    """`14_TRACEABILITY.md`: forward and backward trace must work correctly
    across split (Sortation), merge (Mixing), a plain transformation
    (Grinding), Packing, and a Shipment -- all combined in one chain, not
    tested pairwise in isolation.

    Chain built:
      supplier1 --RECEIVING--> raw_a (100kg)
      supplier2 --RECEIVING--> raw_b (50kg)
      raw_a --SORTATION--> gourmet_a (70kg) + nc_a (30kg)         [split]
      [gourmet_a, raw_b] --MIXING--> mixed (120kg)                [merge]
      nc_a --GRINDING--> powder (30kg)                            [process, ONE->NEW-BATCH]
      [mixed, powder] --PACKING--> packed (150kg)                 [packing, merge]
      packed --DELIVERY--> customer (Shipment)                    [shipment]
    """
    supplier2 = Supplier(supplier_code="099", name="SUPPLIER DUA")
    session.add(supplier2)
    session.flush()

    raw_a = _receive(session, staff_user, supplier, Q("100.000"))
    raw_b = _receive(session, staff_user, supplier2, Q("50.000"))

    sort_event = record_process_event(
        session,
        event_type=EventType.SORTATION,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw_a, quantity=Q("100.000"))],
        outputs=[
            OutputSpec(quantity=Q("70.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
            OutputSpec(quantity=Q("30.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
        ],
    )
    gourmet_a, nc_a = _output_ids(sort_event)

    mix_event = record_process_event(
        session,
        event_type=EventType.MIXING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[
            InputSpec(batch_id=gourmet_a, quantity=Q("70.000")),
            InputSpec(batch_id=raw_b, quantity=Q("50.000")),
        ],
        outputs=[OutputSpec(quantity=Q("120.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED, process_code="03"))],
    )
    mixed = _output_ids(mix_event)[0]

    grind_event = record_process_event(
        session,
        event_type=EventType.GRINDING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=nc_a, quantity=Q("30.000"))],
        outputs=[OutputSpec(quantity=Q("30.000"), new_batch=NewBatchSpec(batch_type=BatchType.POWDER))],
    )
    powder = _output_ids(grind_event)[0]

    pack_event = record_process_event(
        session,
        event_type=EventType.PACKING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[
            InputSpec(batch_id=mixed, quantity=Q("120.000")),
            InputSpec(batch_id=powder, quantity=Q("30.000")),
        ],
        outputs=[OutputSpec(quantity=Q("150.000"), new_batch=NewBatchSpec(batch_type=BatchType.PACKAGED))],
    )
    packed = _output_ids(pack_event)[0]

    customer = Customer(name="MCC Vietnam")
    session.add(customer)
    session.flush()

    record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=packed, quantity=Q("150.000"))],
            gross_weight=Q("152.000"),
            shipping_number="SHP-14-001",
            recipient="MCC Vietnam",
            customer_id=customer.customer_id,
        ),
    )

    result = full_trace(session, packed)

    # Backward: both original suppliers must be reachable, none lost across
    # the split (Sortation) + merge (Mixing) + process (Grinding) + merge
    # (Packing) chain -- this is GENEALOGY.md's "never lose parent batches"
    # rule, now exercised across every relationship shape in one trace.
    assert {s.supplier_id for s in result.suppliers} == {supplier.supplier_id, supplier2.supplier_id}

    # Forward: from packed, the only leaf is packed itself (DELIVERY is a
    # stock-out self-loop, TEST_CASES.md #11), and it must resolve to the
    # Shipment/Customer we recorded.
    assert len(result.shipments) == 1
    assert result.shipments[0].customer_id == customer.customer_id
    assert result.shipments[0].shipping_number == "SHP-14-001"

    # Fully delivered -- nothing left incomplete.
    assert result.incomplete == []

    report = chain_of_custody_report(session, packed)
    assert report["batch"]["batch_id"] == packed
    assert report["batch"]["status"] == "SHIPPED"
    assert {s["supplier_id"] for s in report["suppliers"]} == {supplier.supplier_id, supplier2.supplier_id}
    upstream_types = [e["event_type"] for e in report["upstream_events"]]
    # Every process type in the chain must appear exactly once in the
    # flattened, de-duplicated upstream history.
    assert upstream_types.count("RECEIVING") == 2
    assert upstream_types.count("SORTATION") == 1
    assert upstream_types.count("MIXING") == 1
    assert upstream_types.count("GRINDING") == 1
    assert upstream_types.count("PACKING") == 1
    # Chronological order preserved (all same date here, but event_id tiebreak
    # keeps recording order stable): RECEIVING events must precede PACKING.
    assert upstream_types.index("PACKING") > upstream_types.index("SORTATION")
    assert report["downstream_events"][0]["event_type"] == "DELIVERY"
    assert report["shipments"][0]["customer_name"] == "MCC Vietnam"
    assert report["incomplete_leaves"] == []


def test_resolve_shipments_empty_when_still_in_process(session, staff_user, supplier):
    raw = _receive(session, staff_user, supplier, Q("40.000"))
    forward = full_trace(session, raw).forward
    assert resolve_shipments(session, forward) == []


def test_incomplete_leaves_flags_batches_still_in_process(session, staff_user, supplier):
    raw = _receive(session, staff_user, supplier, Q("30.000"))
    sort_event = record_process_event(
        session,
        event_type=EventType.SORTATION,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw, quantity=Q("30.000"))],
        outputs=[
            OutputSpec(quantity=Q("20.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
            OutputSpec(quantity=Q("10.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
        ],
    )
    outputs = set(_output_ids(sort_event))

    result = full_trace(session, raw)
    assert {b.batch_id for b in result.incomplete} == outputs
    assert result.shipments == []


def test_incomplete_leaves_excludes_rejected_batches(session, staff_user, supplier):
    """REJECTED is a terminal disposition (GENEALOGY.md §3.2) even though
    it is never a Shipment -- a rejected leaf must NOT be reported as
    "still in process"."""
    raw = _receive(session, staff_user, supplier, Q("20.000"))
    record_process_event(
        session,
        event_type=EventType.QC_TEST,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw, quantity=Q("20.000"))],
        outputs=[OutputSpec(batch_id=raw, quantity=Q("20.000"))],
    )
    mark_batch_rejected(session, batch_id=raw, actor_user_id=staff_user.user_id, reason="Kontaminasi logam")

    forward = full_trace(session, raw).forward
    assert incomplete_leaves(forward) == []


def test_full_trace_reaches_supplier_and_customer_two_hop(session, staff_user, supplier):
    """Sanity check against the simpler TEST_CASES.md #10/#11 shape already
    covered by test_genealogy.py, but through the new full_trace()/
    chain_of_custody_report() entry points."""
    raw = _receive(session, staff_user, supplier, Q("50.000"))
    pack_event = record_process_event(
        session,
        event_type=EventType.PACKING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw, quantity=Q("50.000"))],
        outputs=[OutputSpec(quantity=Q("50.000"), new_batch=NewBatchSpec(batch_type=BatchType.PACKAGED))],
    )
    packed = _output_ids(pack_event)[0]

    customer = Customer(name="Liberta Gelato")
    session.add(customer)
    session.flush()

    record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=packed, quantity=Q("50.000"))],
            gross_weight=Q("51.000"),
            customer_id=customer.customer_id,
        ),
    )

    report = chain_of_custody_report(session, raw)
    assert report["batch"]["batch_id"] == raw
    assert [s["supplier_id"] for s in report["suppliers"]] == [supplier.supplier_id]
    assert report["shipments"][0]["customer_name"] == "Liberta Gelato"
