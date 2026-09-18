"""Hijau curing/airdrying endpoints (Fase 20 UI over the Fase 19 engine) --
thin wrappers over `services.curing.record_main_curing` /
`record_first_curing` / `record_second_curing` / `record_third_curing` /
`record_airdrying`. Shrinkage for every stage is derived server-side
(`starting_quantity - final_quantity`, services/curing.py
`_record_curing_stage`) -- the UI must never compute or send its own
shrinkage value, same rule as Sundrying (routers/steam_dry.py).

Five separate endpoints, one per `EventType` (services/curing.py module
docstring #2 -- each stage is its own self-loop event, not one generic
event with a "stage" parameter), even though the field shape is identical
across all five. This mirrors the existing Magnetization/MD Powder
convention (routers/powder.py): both are self-loop inspections with
identical fields but stay as separate endpoints/schemas rather than one
reused class, because each is a distinct business concept the UI history
view filters on independently (app.js STAGE_DEFS/eventTypeMap).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services.curing import (
    CuringStageInput,
    record_airdrying,
    record_first_curing,
    record_main_curing,
    record_second_curing,
    record_third_curing,
)
from ..database import get_db
from ..schemas import (
    AirdryingCreate,
    FirstCuringCreate,
    MainCuringCreate,
    ProcessEventOut,
    SecondCuringCreate,
    ThirdCuringCreate,
)
from ..serializers import event_to_out

router = APIRouter(tags=["curing"])


@router.post("/main-curing", response_model=ProcessEventOut, status_code=201)
def create_main_curing(payload: MainCuringCreate, db: Session = Depends(get_db)):
    data = CuringStageInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
        duration=payload.duration,
        condition_notes=payload.condition_notes,
    )
    event = record_main_curing(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/first-curing", response_model=ProcessEventOut, status_code=201)
def create_first_curing(payload: FirstCuringCreate, db: Session = Depends(get_db)):
    data = CuringStageInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
        duration=payload.duration,
        condition_notes=payload.condition_notes,
    )
    event = record_first_curing(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/second-curing", response_model=ProcessEventOut, status_code=201)
def create_second_curing(payload: SecondCuringCreate, db: Session = Depends(get_db)):
    data = CuringStageInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
        duration=payload.duration,
        condition_notes=payload.condition_notes,
    )
    event = record_second_curing(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/third-curing", response_model=ProcessEventOut, status_code=201)
def create_third_curing(payload: ThirdCuringCreate, db: Session = Depends(get_db)):
    data = CuringStageInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
        duration=payload.duration,
        condition_notes=payload.condition_notes,
    )
    event = record_third_curing(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/airdrying", response_model=ProcessEventOut, status_code=201)
def create_airdrying(payload: AirdryingCreate, db: Session = Depends(get_db)):
    data = CuringStageInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
        duration=payload.duration,
        condition_notes=payload.condition_notes,
    )
    event = record_airdrying(db, data)
    db.flush()
    return event_to_out(db, event)
