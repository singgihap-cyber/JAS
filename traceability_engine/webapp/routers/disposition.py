"""Disposisi batch REJECTED (Fase 26) -- pembungkus tipis atas
`services.disposition`. Tanpa cek role di lapisan ini: server (service) adalah
satu-satunya titik penegakan, `UnauthorizedDispositionError` -> HTTP 403."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import Batch
from ...services.disposition import audit_disposition, return_to_supplier
from ..database import get_db
from ..schemas import DispositionRowOut, ProcessEventOut, SupplierReturnCreate
from ..serializers import event_to_out

router = APIRouter(tags=["disposition"])


@router.post("/batches/{batch_id}/return-to-supplier", response_model=ProcessEventOut, status_code=201)
def create_supplier_return(batch_id: int, payload: SupplierReturnCreate, db: Session = Depends(get_db)):
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    event = return_to_supplier(
        db,
        batch_id=batch_id,
        actor_user_id=payload.actor_user_id,
        reason=payload.reason,
        event_date=payload.event_date,
        quantity=payload.quantity,
        event_time=payload.event_time,
    )
    db.flush()
    return event_to_out(db, event)


@router.get("/audit/disposition", response_model=list[DispositionRowOut])
def get_disposition_audit(batch_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Batch REJECTED + status pengembalian ke supplier + peringatan bila
    dipakai setelah ditolak. Kosong = tidak ada batch REJECTED."""
    return [{**r.__dict__, "message": r.message()} for r in audit_disposition(db, batch_id)]
