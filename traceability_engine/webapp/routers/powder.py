"""Grinding & Sieving, Magnetization, and Metal Detection Powder endpoints --
thin wrappers over `services.powder.record_grinding` /
`record_magnetization` / `record_md_powder`. Grinding is ONE->NEW-BATCH
(always mints a new POWDER batch, services/powder.py #5); Magnetization and
MD Powder are self-loop ONE->ONE inspections (same shape as QC/MD, no
`QualityTest` row -- services/powder.py #9). No accept/reject rule is
applied here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...enums import LinkRole
from ...services.powder import (
    GrindingInput,
    MagnetizationInput,
    MDPowderInput,
    record_grinding,
    record_magnetization,
    record_md_powder,
)
from ..database import get_db
from ..schemas import (
    GrindingCreate,
    GrindingResult,
    MagnetizationCreate,
    MDPowderCreate,
    ProcessEventOut,
)
from ..serializers import batch_to_out, event_to_out

router = APIRouter(tags=["powder"])


@router.post("/grinding", response_model=GrindingResult, status_code=201)
def create_grinding(payload: GrindingCreate, db: Session = Depends(get_db)):
    data = GrindingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        nc_batch_id=payload.batch_id,  # generic "batch_id" from the Proses form -- see schemas.py note
        final_qty=payload.final_qty,
        starting_qty=payload.starting_qty,
        unit=payload.unit,
        result_date=payload.result_date,
        process_code=payload.process_code,
    )
    event = record_grinding(db, data)
    db.flush()
    # GRINDING: one INPUT (NC) + one OUTPUT (new Powder) link -- filter by
    # role rather than indexing, since relationship load order is not
    # guaranteed (same reasoning as routers/mixing.py).
    output_link = next(link for link in event.links if link.role == LinkRole.OUTPUT)
    return GrindingResult(event=event_to_out(db, event), batch=batch_to_out(output_link.batch))


@router.post("/magnetization", response_model=ProcessEventOut, status_code=201)
def create_magnetization(payload: MagnetizationCreate, db: Session = Depends(get_db)):
    data = MagnetizationInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        quantity=payload.quantity,
        unit=payload.unit,
        finding=payload.finding,
        notes=payload.notes,
    )
    event = record_magnetization(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/md-powder", response_model=ProcessEventOut, status_code=201)
def create_md_powder(payload: MDPowderCreate, db: Session = Depends(get_db)):
    data = MDPowderInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        quantity=payload.quantity,
        unit=payload.unit,
        finding=payload.finding,
        notes=payload.notes,
    )
    event = record_md_powder(db, data)
    db.flush()
    return event_to_out(db, event)
