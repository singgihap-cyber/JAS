"""Hijau route endpoints (Fase 19 engine, Fase 20 UI, Fase 22 correction) --
thin wrappers over `services.curing.*`. Shrinkage (Lepas Tangkai, Airdrying)
is derived server-side, never computed/sent by the UI, same rule as
Sundrying (routers/steam_dry.py). Main/1st/2nd/3rd Curing and Blanching are
stock-neutral (curing.py module docstring #1/#3) and never carry a
shrinkage value at all.

Six separate endpoints, one per `EventType` (services/curing.py module
docstring #2 -- each stage is its own self-loop event), even though
Main/1st/2nd/3rd Curing share an identical field shape. This mirrors the
existing Magnetization/MD Powder convention (routers/powder.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services.curing import (
    AirdryingInput,
    BlanchingInput,
    CuringStageInput,
    StemRemovalInput,
    record_airdrying,
    record_blanching,
    record_first_curing,
    record_main_curing,
    record_second_curing,
    record_stem_removal,
    record_third_curing,
)
from ..database import get_db
from ..schemas import (
    AirdryingCreate,
    BlanchingCreate,
    FirstCuringCreate,
    MainCuringCreate,
    ProcessEventOut,
    SecondCuringCreate,
    StemRemovalCreate,
    ThirdCuringCreate,
)
from ..serializers import event_to_out

router = APIRouter(tags=["curing"])


@router.post("/stem-removal", response_model=ProcessEventOut, status_code=201)
def create_stem_removal(payload: StemRemovalCreate, db: Session = Depends(get_db)):
    data = StemRemovalInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
    )
    event = record_stem_removal(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/blanching", response_model=ProcessEventOut, status_code=201)
def create_blanching(payload: BlanchingCreate, db: Session = Depends(get_db)):
    data = BlanchingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        quantity=payload.quantity,
        unit=payload.unit,
        temperature=payload.temperature,
        dip_duration_minutes=payload.dip_duration_minutes,
    )
    event = record_blanching(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/main-curing", response_model=ProcessEventOut, status_code=201)
def create_main_curing(payload: MainCuringCreate, db: Session = Depends(get_db)):
    data = CuringStageInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        quantity=payload.quantity,
        unit=payload.unit,
        duration_hours=payload.duration_hours,
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
        quantity=payload.quantity,
        unit=payload.unit,
        duration_hours=payload.duration_hours,
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
        quantity=payload.quantity,
        unit=payload.unit,
        duration_hours=payload.duration_hours,
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
        quantity=payload.quantity,
        unit=payload.unit,
        duration_hours=payload.duration_hours,
    )
    event = record_third_curing(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/airdrying", response_model=ProcessEventOut, status_code=201)
def create_airdrying(payload: AirdryingCreate, db: Session = Depends(get_db)):
    data = AirdryingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
        duration_days=payload.duration_days,
        final_ka=payload.final_ka,
    )
    event = record_airdrying(db, data)
    db.flush()
    return event_to_out(db, event)
