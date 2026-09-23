"""Forward/Backward Traceability (Fase 14) endpoint -- thin wrapper over
`services.traceability.chain_of_custody_report()`. This is the "trace this
batch end-to-end" report PROJECT_STATUS.md (Fase 15 slice 4) asks the
existing "Batch History" page to grow into, alongside (not instead of) the
per-batch linear event list `GET /batches/{id}` already serves: this
endpoint adds the backward (supplier) and forward (shipment/customer) ends
plus any still-in-process leaves, all assembled by the engine already --
no new traversal or business rule at this layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import Batch
from ...services.traceability import chain_of_custody_report
from ..database import get_db
from ..schemas import ChainOfCustodyOut

router = APIRouter(tags=["traceability"])


@router.get("/batches/{batch_id}/trace", response_model=ChainOfCustodyOut)
def get_batch_trace(batch_id: int, include_void: bool = False, db: Session = Depends(get_db)):
    """Fase 50: event VOID (dibatalkan) disaring secara default;
    `?include_void=true` menampilkan riwayat lengkap termasuk yang dibatalkan."""
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    return chain_of_custody_report(db, batch_id, include_void=include_void)
