"""Sortation (SORT) endpoint -- thin wrapper over
`services.sortation.record_sortation`. ONE->MANY: one new output batch per
grade quantity > 0 supplied, or a single-output re-grade
(Upgrade/Downgrade) when exactly one grade field is filled in -- see
services/sortation.py's module docstring for the confirmed decisions (grade
code mapping, batch_type defaults, inheritance of jenis/supplier/receiving
date, derived shrinkage, process_code meaning) this endpoint defers to. No
grade-mapping or accept/reject rule is applied at this layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...enums import LinkRole
from ...services.sortation import SortationInput, record_sortation
from ..database import get_db
from ..schemas import SortationCreate, SortationResult
from ..serializers import batch_to_out, event_to_out

router = APIRouter(tags=["sortation"])


@router.post("/sortation", response_model=SortationResult, status_code=201)
def create_sortation(payload: SortationCreate, db: Session = Depends(get_db)):
    data = SortationInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        initial_qty=payload.initial_qty,
        unit=payload.unit,
        end_date=payload.end_date,
        gourmet_qty=payload.gourmet_qty,
        eg_qty=payload.eg_qty,
        ep_qty=payload.ep_qty,
        nc_qty=payload.nc_qty,
        powder_qty=payload.powder_qty,
        process_code=payload.process_code,
        gourmet_batch_number=payload.gourmet_batch_number,
        eg_batch_number=payload.eg_batch_number,
        ep_batch_number=payload.ep_batch_number,
        nc_batch_number=payload.nc_batch_number,
        powder_batch_number=payload.powder_batch_number,
        auto_batch_number=payload.auto_batch_number,
        jenis_code=payload.jenis_code,
    )
    event = record_sortation(db, data)
    db.flush()
    # SORTATION is ONE->MANY: every OUTPUT link is a newly minted grade batch
    # (never a self-loop reuse of the input, services/sortation.py #1).
    new_batches = [link.batch for link in event.links if link.role == LinkRole.OUTPUT]
    return SortationResult(
        event=event_to_out(db, event),
        batches=[batch_to_out(b) for b in new_batches],
    )
