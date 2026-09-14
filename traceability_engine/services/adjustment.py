"""The ADJUSTMENT (stock opname) gated path, and manual REJECTED marking.

Both are deliberately kept out of record_process_event() / the genealogy
graph (GENEALOGY.md §3.2, §5.2):

- ADJUSTMENT is the one escape hatch that changes a batch's balance without
  a matching physical transformation, so it is gated to the
  PRODUCTION_MANAGER role, requires a mandatory reason, and always writes an
  AuditLog entry (TEST_CASES.md #13).
- REJECTED is never set automatically (there is no numeric QC/MD
  threshold, GENEALOGY.md §5.1) -- it is always an explicit PIC call to
  mark_batch_rejected().
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import AuditAction, BatchStatus, EventStatus, EventType, LinkRole, TransactionDirection, UserRole
from ..exceptions import InvalidEventStructureError, UnauthorizedAdjustmentError
from ..models import AuditLog, Batch, EventBatchLink, ProcessEvent, StockTransaction, User


def record_adjustment(
    session: Session,
    *,
    batch_id: int,
    new_quantity: Decimal,
    actor_user_id: int,
    notes: str,
    event_date: dt.date,
    event_time: Optional[dt.time] = None,
) -> ProcessEvent:
    """Correct a batch's on-hand quantity (physical stock count, spoilage
    write-off, data-entry fix). Only a User with role PRODUCTION_MANAGER may
    call this (GENEALOGY.md §5.2, confirmed 2026-09-14 -- currently Robiah).
    `notes` (the reason) is mandatory.
    """
    if not notes or not notes.strip():
        raise InvalidEventStructureError(
            "ADJUSTMENT requires a non-empty reason in `notes` (GENEALOGY.md §5.2)."
        )

    actor = session.get(User, actor_user_id)
    if actor is None or actor.role != UserRole.PRODUCTION_MANAGER:
        raise UnauthorizedAdjustmentError(
            f"User {actor_user_id} is not authorized to record an ADJUSTMENT "
            "-- only the PRODUCTION_MANAGER role may perform stock opname "
            "(GENEALOGY.md §5.2, confirmed 2026-09-14)."
        )

    batch = session.get(Batch, batch_id)
    if batch is None:
        raise InvalidEventStructureError(f"Batch {batch_id} does not exist.")

    old_quantity = batch.current_quantity
    delta = new_quantity - old_quantity

    event = ProcessEvent(
        event_type=EventType.ADJUSTMENT,
        event_date=event_date,
        event_time=event_time,
        pic_user_id=actor_user_id,
        notes=notes,
        status=EventStatus.COMPLETED,
        created_by=actor_user_id,
    )
    session.add(event)
    session.flush()

    if delta != 0:
        role = LinkRole.OUTPUT if delta > 0 else LinkRole.INPUT
        session.add(
            EventBatchLink(
                event_id=event.event_id,
                batch_id=batch.batch_id,
                role=role,
                quantity=abs(delta),
                unit=batch.unit,
            )
        )
        session.add(
            StockTransaction(
                batch_id=batch.batch_id,
                event_id=event.event_id,
                direction=TransactionDirection.IN if delta > 0 else TransactionDirection.OUT,
                quantity=abs(delta),
                balance_after=new_quantity,
            )
        )
        batch.current_quantity = new_quantity

    session.add(
        AuditLog(
            entity_type="Batch",
            entity_id=batch.batch_id,
            action=AuditAction.ADJUSTMENT_APPROVED,
            actor_user_id=actor_user_id,
            before_value=str(old_quantity),
            after_value=str(new_quantity),
        )
    )

    session.flush()
    return event


def mark_batch_rejected(session: Session, *, batch_id: int, actor_user_id: int, reason: str) -> Batch:
    """Manually mark a batch REJECTED. Never automated -- QC/MD carry no
    numeric acceptance threshold (GENEALOGY.md §5.1); accept/reject is
    always a PIC judgment call."""
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise InvalidEventStructureError(f"Batch {batch_id} does not exist.")

    old_status = batch.status
    batch.status = BatchStatus.REJECTED

    session.add(
        AuditLog(
            entity_type="Batch",
            entity_id=batch.batch_id,
            action=AuditAction.REJECTED,
            actor_user_id=actor_user_id,
            before_value=str(old_status),
            after_value=f"REJECTED: {reason}",
        )
    )
    session.flush()
    return batch


def mark_batch_superseded(session: Session, *, batch_id: int, actor_user_id: int, reason: str) -> Batch:
    """Manually mark a batch SUPERSEDED (re-graded via Sortation
    Downgrade/Upgrade into a new batch number, GENEALOGY.md §3.1/§3.2).
    Not inferred automatically -- the caller (Fase 7 Sortation handler)
    knows when a re-grade, rather than a plain split/consumption, occurred.
    """
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise InvalidEventStructureError(f"Batch {batch_id} does not exist.")

    old_status = batch.status
    batch.status = BatchStatus.SUPERSEDED

    session.add(
        AuditLog(
            entity_type="Batch",
            entity_id=batch.batch_id,
            action=AuditAction.UPDATE,
            actor_user_id=actor_user_id,
            before_value=str(old_status),
            after_value=f"SUPERSEDED: {reason}",
        )
    )
    session.flush()
    return batch
