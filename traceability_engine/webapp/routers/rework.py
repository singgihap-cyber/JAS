"""Rework (REW) endpoint -- thin wrapper over `services.rework.record_rework`.
ONE->MANY: one new output batch per grade quantity > 0 supplied
(Gourmet/EG/EP/NC, no Powder slot -- see services/rework.py's module
docstring for the confirmed decisions this endpoint defers to), structurally
identical to Sortation's endpoint (routers/sortation.py) except every output
is unconditionally tagged `process_code="04"` (rework.py #5, not exposed as
a caller parameter). No grade-mapping or accept/reject rule is applied at
this layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...enums import LinkRole
from ...services.rework import ReworkInput, record_rework
from ..database import get_db
from ..schemas import ReworkCreate, ReworkResult
from ..serializers import batch_to_out, event_to_out

router = APIRouter(tags=["rework"])


@router.post("/rework", response_model=ReworkResult, status_code=201)
def create_rework(payload: ReworkCreate, db: Session = Depends(get_db)):
    data = ReworkInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        starting_qty=payload.starting_qty,
        unit=payload.unit,
        process_description=payload.process_description,
        gourmet_qty=payload.gourmet_qty,
        eg_qty=payload.eg_qty,
        ep_qty=payload.ep_qty,
        nc_qty=payload.nc_qty,
    )
    event = record_rework(db, data)
    db.flush()
    # REWORK is ONE->MANY like Sortation: every OUTPUT link is a newly minted
    # grade batch (never a self-loop reuse of the input, rework.py module
    # docstring) -- filter by role rather than indexing (routers/mixing.py
    # reasoning, "Keputusan Fase 15 (slice 2)" #2).
    new_batches = [link.batch for link in event.links if link.role == LinkRole.OUTPUT]
    return ReworkResult(
        event=event_to_out(db, event),
        batches=[batch_to_out(b) for b in new_batches],
    )
