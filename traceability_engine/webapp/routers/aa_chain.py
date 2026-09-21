"""Audit perubahan AA sepanjang rute Hijau (Fase 36) + tandai-tinjau dan koreksi
nomor batch (Fase 42) -- pembungkus tipis atas `services.aa_chain` /
`services.aa_review`. Pendeteksian tetap read-only dan tidak memblokir."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import Batch
from ...services.aa_chain import audit_aa_chain
from ...services.aa_review import correct_batch_number, list_number_history, review_aa_finding
from ..database import get_db
from ..schemas import (
    AaChangeFindingOut, AaFindingReviewIn, AaFindingReviewOut,
    BatchNumberCorrectionIn, BatchNumberCorrectionOut,
)

router = APIRouter(tags=["aa-chain"])


@router.get("/audit/aa-chain", response_model=list[AaChangeFindingOut])
def get_aa_chain_audit(
    batch_id: Optional[int] = None,
    hijau_only: bool = True,
    review_status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Batch hasil yang AA-nya (01/02) berbeda dari batch sumber pada event yang
    sama (kecuali Mixing). `hijau_only=true` (default) = hanya rute Hijau.
    `review_status` (Fase 42) = BARU / DITINJAU / DIABAIKAN / DIKOREKSI.
    Kosong = tidak ada perubahan AA."""
    return [
        {**f.__dict__, "message": f.message()}
        for f in audit_aa_chain(db, batch_id, hijau_only, review_status)
    ]


@router.post("/audit/aa-chain/review", response_model=AaFindingReviewOut, status_code=201)
def review_aa_chain_finding(payload: AaFindingReviewIn, db: Session = Depends(get_db)):
    """Fase 42: tandai temuan DITINJAU / DIABAIKAN (Production Manager saja, catatan wajib)."""
    rv = review_aa_finding(
        db, event_id=payload.event_id, source_batch_id=payload.source_batch_id,
        result_batch_id=payload.result_batch_id, status=payload.status,
        actor_user_id=payload.actor_user_id, note=payload.note)
    db.flush()
    return rv


@router.post("/batches/{batch_id}/correct-number", response_model=BatchNumberCorrectionOut, status_code=201)
def correct_number(batch_id: int, payload: BatchNumberCorrectionIn, db: Session = Depends(get_db)):
    """Fase 42: ganti nomor batch (Production Manager saja, alasan wajib, jejak lengkap)."""
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    entry = correct_batch_number(
        db, batch_id=batch_id, new_batch_number=payload.new_batch_number,
        actor_user_id=payload.actor_user_id, reason=payload.reason)
    db.flush()
    return entry


@router.get("/batches/{batch_id}/number-history", response_model=list[BatchNumberCorrectionOut])
def get_number_history(batch_id: int, db: Session = Depends(get_db)):
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    return list_number_history(db, batch_id=batch_id)
