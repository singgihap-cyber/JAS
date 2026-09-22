"""Koreksi tanggal & pembatalan (soft-cancel) event historis (Fase 44) --
pembungkus tipis atas `services.event_correction`. Lihat modul itu untuk
lingkup dan syarat kelayakan (event tanpa turunan saja)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import ProcessEvent
from ...services.event_correction import (
    cancel_event, correct_event_date, list_correctable_events, list_event_correction_history,
)
from ..database import get_db
from ..schemas import (
    CorrectableEventOut, EventCancelIn, EventCancellationOut,
    EventDateCorrectionIn, EventDateCorrectionOut, EventHistoryOut,
)

router = APIRouter(tags=["event-correction"])


@router.get("/events/correctable", response_model=list[CorrectableEventOut])
def get_correctable_events(batch_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Event yang MASIH BOLEH dikoreksi tanggalnya / dibatalkan -- tidak ada
    event/batch turunan setelahnya pada batch yang sama."""
    return list_correctable_events(db, batch_id=batch_id)


@router.post("/events/{event_id}/correct-date", response_model=EventDateCorrectionOut, status_code=201)
def correct_event_date_endpoint(event_id: int, payload: EventDateCorrectionIn, db: Session = Depends(get_db)):
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    entry = correct_event_date(
        db, event_id=event_id, new_event_date=payload.new_event_date,
        actor_user_id=payload.actor_user_id, reason=payload.reason,
    )
    db.flush()
    return entry


@router.post("/events/{event_id}/cancel", response_model=EventCancellationOut, status_code=201)
def cancel_event_endpoint(event_id: int, payload: EventCancelIn, db: Session = Depends(get_db)):
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    entry = cancel_event(
        db, event_id=event_id, actor_user_id=payload.actor_user_id, reason=payload.reason,
    )
    db.flush()
    return entry


@router.get("/events/{event_id}/history", response_model=list[EventHistoryOut])
def get_event_correction_history(event_id: int, db: Session = Depends(get_db)):
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    return list_event_correction_history(db, event_id=event_id)
