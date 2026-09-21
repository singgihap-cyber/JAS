"""Status retur ke supplier DIKIRIM -> DITERIMA (Fase 38) -- pembungkus tipis
atas `services.supplier_return`."""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import ProcessEvent
from ...services.supplier_return import (
    bulk_confirm_returns, cancel_return_confirmation, confirm_return_received, list_return_history,
    list_supplier_returns, supplier_return_reminders,
)
from ..database import get_db
from ..schemas import (
    SupplierReturnBulkConfirm, SupplierReturnCancel, SupplierReturnConfirm, SupplierReturnHistoryOut,
    SupplierReturnReceiptOut, SupplierReturnRemindersOut,
    SupplierReturnRowOut,
)

router = APIRouter(tags=["supplier-returns"])


@router.get("/supplier-returns", response_model=list[SupplierReturnRowOut])
def get_supplier_returns(
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    overdue: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """Semua retur ke supplier + statusnya (DIKIRIM / DITERIMA). `overdue=true`
    = hanya yang sudah lewat batas pengingat (Fase 39)."""
    return [asdict(r) for r in list_supplier_returns(
        db, status=status, supplier_id=supplier_id, batch_id=batch_id, overdue=overdue)]


@router.get("/supplier-returns/reminders", response_model=SupplierReturnRemindersOut)
def get_supplier_return_reminders(db: Session = Depends(get_db)):
    """Pengingat (Fase 39): retur DIKIRIM >= 3 hari yang belum dikonfirmasi
    diterima supplier, tertua dulu. Hanya laporan, tidak memblokir."""
    return asdict(supplier_return_reminders(db))


@router.post(
    "/supplier-returns/bulk-confirm-received",
    response_model=list[SupplierReturnReceiptOut],
    status_code=201,
)
def bulk_confirm_supplier_returns(payload: SupplierReturnBulkConfirm, db: Session = Depends(get_db)):
    """Fase 41: konfirmasi banyak retur sekaligus (Production Manager saja,
    satu tanggal terima, semua-atau-tidak-sama-sekali)."""
    receipts = bulk_confirm_returns(
        db, event_ids=payload.event_ids, received_date=payload.received_date,
        actor_user_id=payload.actor_user_id, note=payload.note)
    db.flush()
    return receipts


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


@router.post(
    "/supplier-returns/{event_id}/cancel-confirmation",
    response_model=SupplierReturnHistoryOut,
    status_code=201,
)
def cancel_supplier_return_confirmation(
    event_id: int, payload: SupplierReturnCancel, db: Session = Depends(get_db)
):
    """Fase 40: batalkan konfirmasi diterima (hanya Production Manager, alasan wajib)."""
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    entry = cancel_return_confirmation(
        db, event_id=event_id, actor_user_id=payload.actor_user_id, reason=payload.reason)
    db.flush()
    return entry


@router.get("/supplier-returns/{event_id}/history", response_model=list[SupplierReturnHistoryOut])
def get_supplier_return_history(event_id: int, db: Session = Depends(get_db)):
    if db.get(ProcessEvent, event_id) is None:
        raise HTTPException(404, f"Event {event_id} not found")
    return list_return_history(db, event_id=event_id)
