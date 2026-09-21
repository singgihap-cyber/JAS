"""Batch listing/detail and generic process-event history endpoints.

These are read-only reporting endpoints over rows the other routers (or
future sessions' routers) already wrote -- no engine logic lives here.
`GET /process-events` in particular is intentionally generic (filters by
`event_type`/`batch_id` only) rather than one bespoke endpoint per stage,
since every stage's history table needs the same shape (event + links +
optional QualityTest) regardless of which `services/*.py` module wrote it
-- future sessions adding Sortation/Mixing/etc. routers get their history
view for free from this one endpoint.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import batch_number as batch_number_mod
from ...enums import BatchStatus, BatchType, EventType
from ...models import Batch, ProcessEvent
from ..database import get_db
from ..schemas import BatchDetailOut, BatchOut, ProcessEventOut
from ..serializers import batch_detail_to_out, batch_to_out, event_to_out

router = APIRouter(tags=["batches"])


@router.get("/batches", response_model=list[BatchOut])
def list_batches(
    status: Optional[str] = Query(None, description="Filter by BatchStatus, e.g. ACTIVE"),
    batch_type: Optional[str] = Query(None, description="Filter by BatchType, e.g. RAW_KERING"),
    db: Session = Depends(get_db),
):
    stmt = select(Batch).order_by(Batch.created_at.desc())
    if status:
        try:
            stmt = stmt.where(Batch.status == BatchStatus(status))
        except ValueError:
            raise HTTPException(422, f"Unknown status {status!r}")
    if batch_type:
        try:
            stmt = stmt.where(Batch.batch_type == BatchType(batch_type))
        except ValueError:
            raise HTTPException(422, f"Unknown batch_type {batch_type!r}")
    return db.execute(stmt).scalars().all()


@router.get("/batches/{batch_id}", response_model=BatchDetailOut)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    return batch_detail_to_out(db, batch)


@router.get("/process-events", response_model=list[ProcessEventOut])
def list_process_events(
    event_type: Optional[str] = Query(None),
    batch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(ProcessEvent).order_by(ProcessEvent.event_date.desc(), ProcessEvent.event_id.desc())
    if event_type:
        try:
            stmt = stmt.where(ProcessEvent.event_type == EventType(event_type))
        except ValueError:
            raise HTTPException(422, f"Unknown event_type {event_type!r}")
    if batch_id is not None:
        from ...models import EventBatchLink

        event_ids = db.execute(
            select(EventBatchLink.event_id).where(EventBatchLink.batch_id == batch_id).distinct()
        ).scalars().all()
        stmt = stmt.where(ProcessEvent.event_id.in_(event_ids))
    events = db.execute(stmt).scalars().all()
    return [event_to_out(db, e) for e in events]


@router.get("/batch-number/parse")
def parse_batch_number(value: str = Query(..., min_length=1)):
    """Best-effort decode of a manually-typed batch number, for the
    Receiving form to show a live breakdown as staff type -- NEVER a
    generator (batch_number.generate() is intentionally unimplemented, see
    batch_number.py). Returns ok=False (not an error) for free-form input
    that doesn't parse -- Receiving accepts that too."""
    try:
        parsed = batch_number_mod.parse(value)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": True,
        "jenis_code": parsed.jenis_code,
        "jenis_label": parsed.jenis_label,
        "jenis_is_legacy": parsed.jenis_is_legacy,
        "grade_code": parsed.grade_code,
        "supplier_code": parsed.supplier_code,
        "supplier_code_width": parsed.supplier_code_width,
        "receiving_date": parsed.receiving_date.isoformat(),
        "process_code": parsed.process_code,
        "process_code_label": batch_number_mod.PROCESS_CODES.get(parsed.process_code),
    }
