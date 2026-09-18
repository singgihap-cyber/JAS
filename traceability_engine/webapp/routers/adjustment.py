"""ADJUSTMENT (stock opname) + manual REJECTED/SUPERSEDED marking -- thin
wrappers over `services.adjustment.*`. All three stay outside the
genealogy graph by design (GENEALOGY.md §3.2/§5.2, adjustment.py module
docstring):

- `record_adjustment()` is the one gated escape hatch that can change a
  batch's balance without a matching physical transformation -- only a User
  with role PRODUCTION_MANAGER may call it, enforced server-side by
  `UnauthorizedAdjustmentError` (already mapped to HTTP 403 in main.py).
  This router performs NO role check of its own and NEVER filters the PIC
  picker by role client-side -- per "Keputusan Fase 15 (slice 1)" (no real
  auth exists yet in this UI), the server is the single point of
  enforcement, exactly like every other business rule in this codebase.
- `mark_batch_rejected()`/`mark_batch_superseded()` are always an explicit,
  manual PIC judgment call (no numeric QC/MD threshold exists, GENEALOGY.md
  §5.1) -- neither is gated to a specific role, matching the engine's own
  signature (no role check inside either function).

`GET /audit-logs` is a new generic report endpoint (same philosophy as the
existing generic `GET /process-events` in routers/batches.py) -- all three
actions above write an `AuditLog` row but only `record_adjustment()` also
writes a `ProcessEvent`, so a single entity_type/entity_id-filterable
AuditLog listing is the one place the UI can show a unified history across
all three, rather than three bespoke endpoints.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import AuditLog, Batch
from ...services.adjustment import mark_batch_rejected, mark_batch_superseded, record_adjustment
from ..database import get_db
from ..schemas import AdjustmentCreate, AuditLogOut, BatchOut, ProcessEventOut, RejectCreate, SupersedeCreate
from ..serializers import audit_log_to_out, batch_to_out, event_to_out

router = APIRouter(tags=["adjustment"])


@router.post("/adjustment", response_model=ProcessEventOut, status_code=201)
def create_adjustment(payload: AdjustmentCreate, db: Session = Depends(get_db)):
    event = record_adjustment(
        db,
        batch_id=payload.batch_id,
        new_quantity=payload.new_quantity,
        actor_user_id=payload.actor_user_id,
        notes=payload.notes,
        event_date=payload.event_date,
        event_time=payload.event_time,
    )
    db.flush()
    return event_to_out(db, event)


@router.post("/batches/{batch_id}/reject", response_model=BatchOut)
def reject_batch(batch_id: int, payload: RejectCreate, db: Session = Depends(get_db)):
    # batch_id comes from the URL path (a resource reference), not a
    # body-validated field -- same fetch-by-id = 404 pattern as the Fase 15
    # slice 4 GET-by-id endpoints ("Keputusan Fase 15 (slice 4)" #5), rather
    # than letting InvalidEventStructureError fall through to a generic 422.
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    batch = mark_batch_rejected(
        db, batch_id=batch_id, actor_user_id=payload.actor_user_id, reason=payload.reason
    )
    db.flush()
    return batch_to_out(batch)


@router.post("/batches/{batch_id}/supersede", response_model=BatchOut)
def supersede_batch(batch_id: int, payload: SupersedeCreate, db: Session = Depends(get_db)):
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    batch = mark_batch_superseded(
        db, batch_id=batch_id, actor_user_id=payload.actor_user_id, reason=payload.reason
    )
    db.flush()
    return batch_to_out(batch)


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(
    entity_type: Optional[str] = Query(None, description="e.g. Batch"),
    entity_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.audit_id.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    logs = db.execute(stmt).scalars().all()
    return [audit_log_to_out(log) for log in logs]
