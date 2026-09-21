"""Status retur ke supplier DIKIRIM -> DITERIMA (Fase 38) -- pembungkus tipis
atas `services.supplier_return`."""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import ProcessEvent
from ...services.supplier_return import confirm_return_received, list_supplier_returns
from ..database import get_db
from ..schemas import SupplierReturnConfirm, SupplierReturnReceiptOut, SupplierReturnRowOut

router = APIRouter(tags=["supplier-returns"])


@router.get("/supplier-returns", response_model=list[SupplierReturnRowOut])
def get_supplier_returns(
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Semua retur ke supplier + statusnya (DIKIRIM / DITERIMA)."""
    return [asdict(r) for r in list_supplier_returns(
        db, status=status, supplier_id=supplier_id, batch_id=batch_id)]


@router.post(
    "/supplier-returns/{event_id}/confirm-received",
    response_model=SupplierReturnReceiptOut,
    status_code=201,
)
def confirm_supplier_return_received(
    event_id: int, payload: SupplierReturnConfirm, db: Session = Depends(get_db)
):
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    receipt = confirm_return_received(
        db,
        event_id=event_id,
        received_date=payload.received_date,
        actor_user_id=payload.actor_user_id,
        note=payload.note,
    )
    db.flush()
    return receipt
