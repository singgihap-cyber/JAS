"""Steaming and Sundrying endpoints -- thin wrappers over
`services.steam_dry.record_steaming` / `record_sundrying`. Shrinkage for
Sundrying is derived server-side (`starting_quantity - final_quantity`,
services/steam_dry.py docstring #2) -- the UI must never compute or send
its own shrinkage value.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services.steam_dry import SteamingInput, SundryingInput, record_steaming, record_sundrying
from ..database import get_db
from ..schemas import ProcessEventOut, SteamingCreate, SundryingCreate
from ..serializers import event_to_out

router = APIRouter(tags=["steam-dry"])


@router.post("/steaming", response_model=ProcessEventOut, status_code=201)
def create_steaming(payload: SteamingCreate, db: Session = Depends(get_db)):
    data = SteamingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        quantity=payload.quantity,
        unit=payload.unit,
        end_time=payload.end_time,
        pan_count=payload.pan_count,
        water_condition=payload.water_condition,
        pan_condition=payload.pan_condition,
        steam_temperature=payload.steam_temperature,
        verification_reading_1=payload.verification_reading_1,
        verification_reading_2=payload.verification_reading_2,
        verification_reading_3=payload.verification_reading_3,
    )
    event = record_steaming(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/sundrying", response_model=ProcessEventOut, status_code=201)
def create_sundrying(payload: SundryingCreate, db: Session = Depends(get_db)):
    data = SundryingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        final_quantity=payload.final_quantity,
        starting_quantity=payload.starting_quantity,
        unit=payload.unit,
        starting_ka=payload.starting_ka,
        drying_duration=payload.drying_duration,
    )
    event = record_sundrying(db, data)
    db.flush()
    return event_to_out(db, event)
