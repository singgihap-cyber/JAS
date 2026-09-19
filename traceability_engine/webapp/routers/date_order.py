"""Audit urutan tanggal antar-event (Fase 25) -- pembungkus tipis read-only
atas `services.date_order`. Tidak menyimpan apa pun dan tidak menambah aturan
bisnis di lapisan ini."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services.date_order import audit_date_order
from ..database import get_db
from ..schemas import DateOrderViolationOut

router = APIRouter(tags=["date-order"])


@router.get("/audit/date-order", response_model=list[DateOrderViolationOut])
def get_date_order_audit(batch_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Event yang tanggalnya lebih awal dari event yang lebih dulu tercatat
    pada batch yang sama. Kosong = urutan tanggal konsisten."""
    return [{**v.__dict__, "message": v.message()} for v in audit_date_order(db, batch_id)]
