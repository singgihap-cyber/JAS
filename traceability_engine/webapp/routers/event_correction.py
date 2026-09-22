"""Koreksi tanggal & pembatalan (soft-cancel) event historis (Fase 44) --
pembungkus tipis atas `services.event_correction`. Lihat modul itu untuk
lingkup dan syarat kelayakan (event tanpa turunan saja)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import ProcessEvent
from ...services.event_correction import (
    cancel_event, correct_event_date, correct_event_notes, correct_event_quantity, get_blocking_chain,
    list_correctable_events, list_event_correction_history, list_event_links,
)
from ..database import get_db
from ..schemas import (
    BlockingChainOut, CorrectableEventOut, EventCancelIn, EventCancellationOut,
    EventDateCorrectionIn, EventDateCorrectionOut, EventHistoryOut, EventLinkOut,
    EventNotesCorrectionIn, EventNotesCorrectionOut, EventQuantityCorrectionIn,
    EventQuantityCorrectionOut,
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


@router.get("/events/{event_id}/links", response_model=list[EventLinkOut])
def get_event_links(event_id: int, db: Session = Depends(get_db)):
    """Fase 45 -- semua EventBatchLink (INPUT & OUTPUT) satu event, dipakai
    UI untuk memilih link mana yang kuantitasnya mau dikoreksi."""
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    return list_event_links(db, event_id=event_id)


@router.post(
    "/events/{event_id}/correct-quantity", response_model=EventQuantityCorrectionOut, status_code=201
)
def correct_event_quantity_endpoint(
    event_id: int, payload: EventQuantityCorrectionIn, db: Session = Depends(get_db)
):
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    entry = correct_event_quantity(
        db, event_id=event_id, link_id=payload.link_id, new_quantity=payload.new_quantity,
        actor_user_id=payload.actor_user_id, reason=payload.reason,
    )
    db.flush()
    return entry


@router.get("/events/{event_id}/blocking-chain", response_model=BlockingChainOut)
def get_event_blocking_chain(event_id: int, db: Session = Depends(get_db)):
    """Fase 45 -- rantai transitif event turunan yang harus dibatalkan
    lebih dulu (cascade manual bertahap, bukan otomatis) sebelum
    `event_id` sendiri bisa dikoreksi/dibatalkan."""
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    return get_blocking_chain(db, event_id=event_id)


@router.post("/events/{event_id}/correct-notes", response_model=EventNotesCorrectionOut, status_code=201)
def correct_event_notes_endpoint(event_id: int, payload: EventNotesCorrectionIn, db: Session = Depends(get_db)):
    """Fase 46 -- timpa catatan event historis (PM, alasan wajib, tanpa turunan)."""
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    entry = correct_event_notes(
        db, event_id=event_id, new_notes=payload.new_notes,
        actor_user_id=payload.actor_user_id, reason=payload.reason,
    )
    db.flush()
    return entry
