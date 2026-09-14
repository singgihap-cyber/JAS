import datetime as dt
from decimal import Decimal

from traceability_engine.enums import BatchType, EventType
from traceability_engine.models import Customer, Shipment, Supplier
from traceability_engine.services.events import InputSpec, NewBatchSpec, OutputSpec, record_process_event
from traceability_engine.services.genealogy import backward_trace, find_terminal_leaves, forward_trace, resolve_suppliers

TODAY = dt.date(2026, 9, 14)


def _receive(session, staff_user, supplier, qty):
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
    return [l.batch_id for l in event.links][0]


# --- TEST_CASES.md #10: Backward trace reaches supplier -------------------

def test_backward_trace_reaches_supplier_through_chain(session, staff_user, supplier):
    raw = _receive(session, staff_user, supplier, Decimal("100.000"))

    sort_event = record_process_event(
        session,
        event_type=EventType.SORTATION,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw, quantity=Decimal("100.000"))],
        outputs=[OutputSpec(quantity=Decimal("100.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED))],
    )
    graded = [l.batch_id for l in sort_event.links if l.role.value == "OUTPUT"][0]

    node = backward_trace(session, graded)
    assert node.batch.batch_id == graded
    assert len(node.parents) == 1
    assert node.parents[0].batch.batch_id == raw
    assert node.parents[0].produced_by_event.event_type == EventType.RECEIVING

    suppliers = resolve_suppliers(node)
    assert [s.supplier_id for s in suppliers] == [supplier.supplier_id]


def test_backward_trace_mixing_keeps_all_parents(session, staff_user, supplier):
    """GENEALOGY.md §7.1 worked example: Mixing must never lose a parent batch."""
    b1 = _receive(session, staff_user, supplier, Decimal("10.000"))
    b2 = _receive(session, staff_user, supplier, Decimal("20.000"))
    b3 = _receive(session, staff_user, supplier, Decimal("15.000"))

    mix_event = record_process_event(
        session,
        event_type=EventType.MIXING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[
            InputSpec(batch_id=b1, quantity=Decimal("10.000")),
            InputSpec(batch_id=b2, quantity=Decimal("20.000")),
            InputSpec(batch_id=b3, quantity=Decimal("15.000")),
        ],
        outputs=[OutputSpec(quantity=Decimal("45.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED, process_code="03"))],
    )
    mixed = [l.batch_id for l in mix_event.links if l.role.value == "OUTPUT"][0]

    node = backward_trace(session, mixed)
    assert {p.batch.batch_id for p in node.parents} == {b1, b2, b3}


# --- TEST_CASES.md #11: Forward trace reaches shipment/customer -----------

def test_forward_trace_reaches_customer_via_delivery(session, staff_user, supplier):
    raw = _receive(session, staff_user, supplier, Decimal("50.000"))

    pack_event = record_process_event(
        session,
        event_type=EventType.PACKING,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw, quantity=Decimal("50.000"))],
        outputs=[OutputSpec(quantity=Decimal("50.000"), new_batch=NewBatchSpec(batch_type=BatchType.PACKAGED))],
    )
    packed = [l.batch_id for l in pack_event.links if l.role.value == "OUTPUT"][0]

    customer = Customer(name="MCC Vietnam")
    session.add(customer)
    session.flush()

    delivery_event = record_process_event(
        session,
        event_type=EventType.DELIVERY,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=packed, quantity=Decimal("50.000"))],
        outputs=[],
    )
    session.add(Shipment(event_id=delivery_event.event_id, customer_id=customer.customer_id, shipping_number="SHP-001"))
    session.flush()

    node = forward_trace(session, raw)
    assert len(node.children) == 1
    packed_node = node.children[0]
    assert packed_node.batch.batch_id == packed
    assert any(e.event_type == EventType.DELIVERY for e in packed_node.applied_events)

    shipment = session.query(Shipment).filter_by(event_id=delivery_event.event_id).one()
    assert shipment.customer_id == customer.customer_id


def test_forward_trace_through_self_loop_inspection(session, staff_user, supplier):
    raw = _receive(session, staff_user, supplier, Decimal("20.000"))
    record_process_event(
        session,
        event_type=EventType.QC_TEST,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw, quantity=Decimal("20.000"))],
        outputs=[OutputSpec(batch_id=raw, quantity=Decimal("20.000"))],
    )
    node = forward_trace(session, raw)
    assert node.children == []
    assert len(node.applied_events) == 1
    assert node.applied_events[0].event_type == EventType.QC_TEST


def test_find_terminal_leaves(session, staff_user, supplier):
    raw = _receive(session, staff_user, supplier, Decimal("30.000"))
    sort_event = record_process_event(
        session,
        event_type=EventType.SORTATION,
        event_date=TODAY,
        pic_user_id=staff_user.user_id,
        inputs=[InputSpec(batch_id=raw, quantity=Decimal("30.000"))],
        outputs=[
            OutputSpec(quantity=Decimal("20.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
            OutputSpec(quantity=Decimal("10.000"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED)),
        ],
    )
    outputs = [l.batch_id for l in sort_event.links if l.role.value == "OUTPUT"]

    node = forward_trace(session, raw)
    leaves = find_terminal_leaves(node)
    assert {b.batch_id for b in leaves} == set(outputs)
