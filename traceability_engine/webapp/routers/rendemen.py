"""Rendemen Sortasi (Fase 24) -- thin read-only wrapper over
`services.rendemen`. Every figure is derived from genealogy on request;
nothing is stored and no business rule is added at this layer (see
services/rendemen.py for the decisions: ratio not percent, numerator =
received net weight, denominator = sortation OUTPUT total).
Fase 35 adds per-Mixing rendemen (output / total input sumber)."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...enums import EventType
from ...models import ProcessEvent
from ...services.rendemen import (
    list_mixing_rendemen, list_sortation_rendemen, mixing_rendemen, sortation_rendemen,
)
from ..database import get_db
from ..schemas import MixingRendemenOut, SortationRendemenOut

router = APIRouter(tags=["rendemen"])


@router.get("/rendemen/sortation", response_model=list[SortationRendemenOut])
def get_sortation_rendemen_list(
    batch_id: Optional[int] = None,
    date_from: Optional[dt.date] = None,
    date_to: Optional[dt.date] = None,
    db: Session = Depends(get_db),
):
    """One row per sortation event (the SORT sheet's rows), oldest first."""
    return [asdict(r) for r in list_sortation_rendemen(db, batch_id, date_from, date_to)]


@router.get("/rendemen/sortation/{event_id}", response_model=SortationRendemenOut)
def get_sortation_rendemen(event_id: int, db: Session = Depends(get_db)):
    event = db.get(ProcessEvent, event_id)
    if event is None or event.event_type != EventType.SORTATION:
        raise HTTPException(404, f"Sortation event {event_id} not found")
    return asdict(sortation_rendemen(db, event_id))


@router.get("/rendemen/mixing", response_model=list[MixingRendemenOut])
def get_mixing_rendemen_list(
    batch_id: Optional[int] = None,
    date_from: Optional[dt.date] = None,
    date_to: Optional[dt.date] = None,
    db: Session = Depends(get_db),
):
    """One row per mixing event, oldest first (Fase 35)."""
    return [asdict(r) for r in list_mixing_rendemen(db, batch_id, date_from, date_to)]


@router.get("/rendemen/mixing/{event_id}", response_model=MixingRendemenOut)
def get_mixing_rendemen(event_id: int, db: Session = Depends(get_db)):
    event = db.get(ProcessEvent, event_id)
    if event is None or event.event_type != EventType.MIXING:
        raise HTTPException(404, f"Mixing event {event_id} not found")
    return asdict(mixing_rendemen(db, event_id))
