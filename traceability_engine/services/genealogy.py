"""Forward/backward traceability -- GENEALOGY.md §7.

Both directions are graph walks, but this implementation takes a shortcut
that GENEALOGY.md's surrogate-key design (§1) makes available: since every
batch records `created_from_event_id` at creation time, backward traversal
doesn't need to search EventBatchLink for "the event that produced this
batch" -- it's already on the Batch row. This avoids ambiguity around
self-loop events (QC/MD/Steaming/Sundrying), which repeatedly add OUTPUT
rows for the *same* batch_id and would otherwise need special-casing to
avoid being mistaken for new parents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import EventType, LinkRole
from ..models import Batch, EventBatchLink, ProcessEvent, Supplier


@dataclass
class BackwardNode:
    batch: Batch
    produced_by_event: Optional[ProcessEvent]
    parents: list["BackwardNode"] = field(default_factory=list)


def backward_trace(session: Session, batch_id: int) -> BackwardNode:
    """Walk from `batch_id` back to its ultimate source batch(es).

    A RECEIVING-created batch has no inputs (NO_INPUT_EVENT_TYPES), so
    recursion naturally terminates there without special-casing "reached a
    RECEIVING event" -- its `parents` list is simply empty.
    """
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")

    node = BackwardNode(batch=batch, produced_by_event=None, parents=[])

    if batch.created_from_event_id is not None:
        event = session.get(ProcessEvent, batch.created_from_event_id)
        node.produced_by_event = event

        input_links = session.execute(
            select(EventBatchLink).where(
                EventBatchLink.event_id == event.event_id,
                EventBatchLink.role == LinkRole.INPUT,
            )
        ).scalars().all()

        for link in input_links:
            if link.batch_id == batch.batch_id:
                continue  # self-loop safety net; shouldn't occur for a creating event
            node.parents.append(backward_trace(session, link.batch_id))

    return node


def resolve_suppliers(node: BackwardNode) -> list[Supplier]:
    """Collect the distinct Supplier(s) reached at the roots of a backward
    trace (i.e. batches created directly by a RECEIVING event)."""
    suppliers: dict[int, Supplier] = {}

    def _walk(n: BackwardNode) -> None:
        is_root = not n.parents
        if is_root and n.produced_by_event is not None:
            if n.produced_by_event.event_type == EventType.RECEIVING and n.batch.supplier_id:
                suppliers[n.batch.supplier_id] = n.batch.supplier
        for p in n.parents:
            _walk(p)

    _walk(node)
    return list(suppliers.values())


@dataclass
class ForwardNode:
    batch: Batch
    # Events applied to this exact batch_id (self-loop inspections, and any
    # terminal stock-out event such as DELIVERY/SAMPLE_DELIVERY that consumes
    # it without producing a different batch).
    applied_events: list[ProcessEvent] = field(default_factory=list)
    children: list["ForwardNode"] = field(default_factory=list)


def forward_trace(session: Session, batch_id: int) -> ForwardNode:
    """Walk from `batch_id` forward through every event that consumes it or
    a descendant, until reaching SHIPPED/REJECTED terminal batches."""
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")

    node = ForwardNode(batch=batch)

    input_links = session.execute(
        select(EventBatchLink).where(
            EventBatchLink.batch_id == batch_id,
            EventBatchLink.role == LinkRole.INPUT,
        )
    ).scalars().all()

    seen_events: set[int] = set()
    for link in input_links:
        if link.event_id in seen_events:
            continue
        seen_events.add(link.event_id)
        event = session.get(ProcessEvent, link.event_id)

        output_links = session.execute(
            select(EventBatchLink).where(
                EventBatchLink.event_id == event.event_id,
                EventBatchLink.role == LinkRole.OUTPUT,
            )
        ).scalars().all()

        distinct_outputs = [ol for ol in output_links if ol.batch_id != batch_id]

        if not distinct_outputs:
            # Self-loop (QC/MD/Steaming/...) or a stock-out event
            # (DELIVERY/SAMPLE_DELIVERY, which has no outputs at all) --
            # either way this is history on the same node, not a new child.
            node.applied_events.append(event)
        else:
            node.applied_events.append(event)
            for ol in distinct_outputs:
                node.children.append(forward_trace(session, ol.batch_id))

    return node


def find_terminal_leaves(node: ForwardNode) -> list[Batch]:
    """Collect batches at the end of every forward path (no further
    children) -- typically SHIPPED or REJECTED."""
    if not node.children:
        return [node.batch]
    leaves: list[Batch] = []
    for child in node.children:
        leaves.extend(find_terminal_leaves(child))
    return leaves
