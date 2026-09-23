"""Fase 14 -- Forward/Backward Traceability (user-facing query/report layer).

`14_TRACEABILITY.md`: "Implement forward and backward trace across
split/merge genealogy, processes, packing and shipments. Test and
checkpoint phase-14-traceability."

**This is NOT the traversal algorithm itself.** `services/genealogy.py`
(`backward_trace`, `forward_trace`, `resolve_suppliers`,
`find_terminal_leaves`) has implemented the actual graph walk since
Fase 2/3, grounded directly in `GENEALOGY.md` §7, and already has its own
tests (`tests/test_genealogy.py`) covering `TEST_CASES.md` #10 ("Backward
trace reaches supplier") and #11 ("Forward trace reaches shipment/
customer") across split (Sortation), merge (Mixing) and shipment
(Delivery/self-loop QC) genealogy. Per `PROJECT_STATUS.md`'s own framing of
this phase's likely scope (the same "don't guess the scope from the title
alone" instruction that shaped Fase 13's approach to `13_STOCK.md`), the
new work here is the layer *on top of* that engine: a single "trace this
batch end-to-end" entry point with structured/summarized output, a
chain-of-custody report, and a validation/report over trace results --
not a second implementation of the walk.

Concretely, three gaps existed between the raw `BackwardNode`/`ForwardNode`
trees and something a report or UI could consume directly:

1. **No forward-direction mirror of `resolve_suppliers()`.** Backward
   trace already resolves the `Supplier` rows at its roots; nothing
   resolved the `Shipment`/`Customer` rows at forward trace's terminal
   leaves, even though `GENEALOGY.md` §7 names "Shipment/Customer" as the
   forward direction's destination symmetrically to "Supplier" as the
   backward direction's. `resolve_shipments()` below is that mirror.
2. **No single call traces a batch in both directions at once.** Every
   caller so far (`tests/test_genealogy.py`) has called `backward_trace()`
   and `forward_trace()` separately. `full_trace()` combines both (plus
   supplier/shipment resolution) into one result, matching the everyday
   question a chain-of-custody request actually asks: "where did this lot
   come from, and where did it (and everything derived from it) end up?"
3. **No way to tell an in-progress trace from a finished one.** A forward
   trace's leaves are just "no further children" -- that includes a batch
   genuinely sitting in stock mid-pipeline (nothing has consumed it *yet*)
   exactly as often as it includes a batch that reached a real terminal
   disposition (`SHIPPED` via Delivery/Sample Delivery, or `REJECTED` via
   QC/MD). `incomplete_leaves()` below is the validation/report step that
   tells those apart, per `GENEALOGY.md` §3.2's own status vocabulary --
   it invents no new status or business rule, it only classifies the
   leaves `forward_trace()` already returns.

`chain_of_custody_report()` assembles all of the above into one
JSON-shaped dict: batch identity, the chronological event history in both
directions (deduplicated, sorted by `event_date`), resolved suppliers,
resolved shipments/customers, and any still-in-process leaves. This is the
"laporan chain-of-custody" / "query trace batch ini dari ujung ke ujung
dengan output terstruktur/ringkas" PROJECT_STATUS.md anticipated -- built
from data the engine has recorded since Fase 2/3, no new schema, no new
business rule.

## Why events are re-sorted rather than trusted in tree-walk order

`backward_trace()`'s recursion naturally visits parents before children
are appended in a way that is close to root-to-batch order, but with
multiple parents (Mixing) or multiple children (Sortation/Rework) the
tree-walk order across *branches* is not guaranteed to be chronological.
`_collect_backward_events()`/`_collect_forward_events()` flatten the tree
into a flat list first, then `chain_of_custody_report()` sorts by
`(event_date, event_id)` and de-duplicates by `event_id` -- an event can
otherwise appear once per branch that touches it (e.g. a Mixing event is
the `produced_by_event` of exactly one node, so no duplication risk there,
but a defensive de-dupe costs nothing and protects against any future
traversal change).

## Why `resolve_shipments()` walks every node, not just `find_terminal_leaves()`

A Delivery/Sample Delivery event is `NO_OUTPUT_EVENT_TYPES` (no output
batch, GENEALOGY.md §3.1/§5.2), so `forward_trace()` records it as an
`applied_events` entry on the *same* node rather than creating a new child
-- exactly like a QC/MD self-loop. That means the node carrying a shipment
is always a leaf (nothing consumes a batch after it ships), but it is
reached the same way as any other node: by walking the tree, not by a
separate lookup. `resolve_shipments()` therefore walks the whole tree and
inspects every node's `applied_events`, the same shape `forward_trace()`
already produces -- it does not need `find_terminal_leaves()` at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import BatchStatus, EventStatus, EventType, LinkRole
from ..models import Batch, Customer, EventBatchLink, ProcessEvent, Shipment, Supplier
from .genealogy import (
    BackwardNode,
    ForwardNode,
    backward_trace,
    forward_trace,
    resolve_suppliers,
)

# Terminal statuses a forward-trace leaf can legitimately end at without
# further action being expected (GENEALOGY.md §3.2). Anything else at a
# leaf means the physical lot is still sitting somewhere in the pipeline.
_TERMINAL_LEAF_STATUSES = frozenset({BatchStatus.SHIPPED, BatchStatus.REJECTED})

# Event types that hand a batch off to an external party (GENEALOGY.md
# §3.1 STOCK-OUT) -- the only ones that can have a Shipment satellite row.
_SHIPMENT_EVENT_TYPES = frozenset({EventType.DELIVERY, EventType.SAMPLE_DELIVERY})


def resolve_shipments(session: Session, node: ForwardNode) -> list[Shipment]:
    """Collect the distinct `Shipment` row(s) reached anywhere in a forward
    trace -- the mirror of `genealogy.resolve_suppliers()` for the forward
    direction. Walks every node (not just terminal leaves -- see module
    docstring) looking for a DELIVERY/SAMPLE_DELIVERY `applied_event`, then
    fetches its `Shipment` satellite (`GENEALOGY.md` §6)."""
    event_ids: list[int] = []

    def _walk(n: ForwardNode) -> None:
        for event in n.applied_events:
            if event.event_type in _SHIPMENT_EVENT_TYPES:
                event_ids.append(event.event_id)
        for child in n.children:
            _walk(child)

    _walk(node)
    if not event_ids:
        return []

    rows = session.execute(
        select(Shipment).where(Shipment.event_id.in_(event_ids))
    ).scalars().all()
    # Preserve discovery order and de-duplicate (a given event_id can only
    # produce one Shipment row in practice, but this stays defensive).
    by_event_id = {s.event_id: s for s in rows}
    return [by_event_id[eid] for eid in dict.fromkeys(event_ids) if eid in by_event_id]


def incomplete_leaves(node: ForwardNode) -> list[Batch]:
    """Terminal leaves of a forward trace whose batch has NOT reached a
    disposition (`SHIPPED` or `REJECTED`, `GENEALOGY.md` §3.2). These are
    batches genuinely still in process (on-hand `ACTIVE` stock, or -- in
    principle -- `SUPERSEDED`/`CONSUMED` rows that end a trace without a
    further recorded event, which would itself indicate a data gap since
    those statuses are supposed to always be followed by the event that
    consumed them). This is a report over `forward_trace()`'s own output,
    not a new traversal or a new status."""
    leaves: list[Batch] = []

    def _walk(n: ForwardNode) -> None:
        if not n.children:
            if n.batch.status not in _TERMINAL_LEAF_STATUSES:
                leaves.append(n.batch)
        else:
            for child in n.children:
                _walk(child)

    _walk(node)
    return leaves


@dataclass
class FullTraceResult:
    """The complete chain-of-custody picture for one batch: everything it
    came from (backward) and everything it became (forward), already
    resolved down to Supplier/Shipment rows and flagged for any
    still-in-process ends."""

    batch: Batch
    backward: BackwardNode
    forward: ForwardNode
    suppliers: list[Supplier] = field(default_factory=list)
    shipments: list[Shipment] = field(default_factory=list)
    incomplete: list[Batch] = field(default_factory=list)


def full_trace(session: Session, batch_id: int, *, include_void: bool = False) -> FullTraceResult:
    """Trace `batch_id` in both directions at once (`GENEALOGY.md` §7) and
    resolve both ends: `Supplier` row(s) at the backward roots,
    `Shipment` row(s) at the forward leaves, and any forward leaves that
    have not yet reached a terminal disposition."""
    backward = backward_trace(session, batch_id, include_void=include_void)
    forward = forward_trace(session, batch_id, include_void=include_void)

    return FullTraceResult(
        batch=backward.batch,
        backward=backward,
        forward=forward,
        suppliers=resolve_suppliers(backward),
        shipments=resolve_shipments(session, forward),
        incomplete=incomplete_leaves(forward),
    )


def _collect_backward_events(node: BackwardNode, acc: list[ProcessEvent]) -> None:
    for parent in node.parents:
        _collect_backward_events(parent, acc)
    if node.produced_by_event is not None:
        acc.append(node.produced_by_event)


def _collect_forward_events(node: ForwardNode, acc: list[ProcessEvent]) -> None:
    acc.extend(node.applied_events)
    for child in node.children:
        _collect_forward_events(child, acc)


def _dedupe_sorted_events(events: list[ProcessEvent]) -> list[ProcessEvent]:
    by_id = {e.event_id: e for e in events}
    return sorted(by_id.values(), key=lambda e: (e.event_date, e.event_id))


def _event_summary(session: Session, event: ProcessEvent) -> dict:
    input_qty = session.execute(
        select(EventBatchLink.quantity).where(
            EventBatchLink.event_id == event.event_id,
            EventBatchLink.role == LinkRole.INPUT,
        )
    ).scalars().all()
    output_qty = session.execute(
        select(EventBatchLink.quantity).where(
            EventBatchLink.event_id == event.event_id,
            EventBatchLink.role == LinkRole.OUTPUT,
        )
    ).scalars().all()
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "event_date": event.event_date.isoformat(),
        "status": event.status.value,
        "pic": event.pic.name if event.pic is not None else None,
        "notes": event.notes,
        "total_input_quantity": sum(input_qty, Decimal("0")) if input_qty else None,
        "total_output_quantity": sum(output_qty, Decimal("0")) if output_qty else None,
    }


def _supplier_summary(supplier: Supplier) -> dict:
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "name": supplier.name,
    }


def _shipment_summary(session: Session, shipment: Shipment) -> dict:
    customer: Optional[Customer] = (
        session.get(Customer, shipment.customer_id) if shipment.customer_id is not None else None
    )
    return {
        "shipment_id": shipment.shipment_id,
        "shipping_number": shipment.shipping_number,
        "customer_name": customer.name if customer is not None else None,
        # Free-text recipient/destination are always recorded regardless of
        # whether customer_id is set -- see delivery.py module docstring #7.
        "recipient": shipment.recipient,
        "destination": shipment.destination,
    }


def _batch_summary(batch: Batch) -> dict:
    return {
        "batch_id": batch.batch_id,
        "batch_number": batch.batch_number,
        "status": batch.status.value,
        "current_quantity": batch.current_quantity,
    }


def _count_void_events(session: Session, batch_ids: set[int]) -> int:
    """Fase 50: jumlah event VOID yang menyentuh batch mana pun di hasil
    telusur -- supaya laporan menyebut ada yang disaring."""
    if not batch_ids:
        return 0
    ids = session.execute(
        select(EventBatchLink.event_id)
        .join(ProcessEvent, ProcessEvent.event_id == EventBatchLink.event_id)
        .where(EventBatchLink.batch_id.in_(batch_ids), ProcessEvent.status == EventStatus.VOID)
    ).scalars().all()
    return len(set(ids))


def _backward_batch_ids(node: BackwardNode, acc: set[int]) -> None:
    acc.add(node.batch.batch_id)
    for p in node.parents:
        _backward_batch_ids(p, acc)


def _forward_batch_ids(node: ForwardNode, acc: set[int]) -> None:
    acc.add(node.batch.batch_id)
    for c in node.children:
        _forward_batch_ids(c, acc)


def chain_of_custody_report(session: Session, batch_id: int, *, include_void: bool = False) -> dict:
    """Build the structured, end-to-end chain-of-custody report for
    `batch_id`: where the material came from, the full ordered event
    history in both directions, where it (and everything derived from it)
    ended up, and anything still in process. This is the report layer
    `PROJECT_STATUS.md` anticipated for Fase 14 -- everything here is
    assembled from `full_trace()`'s already-resolved result, no new
    traversal.
    """
    result = full_trace(session, batch_id, include_void=include_void)

    upstream_events = []
    _collect_backward_events(result.backward, upstream_events)
    upstream_events = _dedupe_sorted_events(upstream_events)

    downstream_events = []
    _collect_forward_events(result.forward, downstream_events)
    downstream_events = _dedupe_sorted_events(downstream_events)

    batch_ids: set[int] = set()
    _backward_batch_ids(result.backward, batch_ids)
    _forward_batch_ids(result.forward, batch_ids)

    return {
        "batch": _batch_summary(result.batch),
        "suppliers": [_supplier_summary(s) for s in result.suppliers],
        "upstream_events": [_event_summary(session, e) for e in upstream_events],
        "downstream_events": [_event_summary(session, e) for e in downstream_events],
        "shipments": [_shipment_summary(session, s) for s in result.shipments],
        "incomplete_leaves": [_batch_summary(b) for b in result.incomplete],
        # Fase 50: event VOID disaring kecuali include_void=True.
        "include_void": include_void,
        "voided_origin": result.backward.voided_origin,
        "void_events_excluded": 0 if include_void else _count_void_events(session, batch_ids),
    }
