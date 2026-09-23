"""Audit perubahan AA sepanjang rute Hijau (Fase 36) + tandai-tinjau dan koreksi
nomor batch (Fase 42) -- pembungkus tipis atas `services.aa_chain` /
`services.aa_review`. Pendeteksian tetap read-only dan tidak memblokir."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import Batch, User
from ...services.aa_chain import audit_aa_chain
from ...services.aa_review import correct_batch_number, list_number_history, review_aa_finding
from ...services.jenis_correction import (
    correct_batch_jenis, list_jenis_corrections, plan_jenis_correction,
)
from ..database import get_db
from ..dependencies import get_current_user, require_actor_matches
from ..schemas import (
    AaChangeFindingOut, AaFindingReviewIn, AaFindingReviewOut,
    BatchNumberCorrectionIn, BatchNumberCorrectionOut, JenisCorrectionIn, JenisCorrectionOut,
    JenisCorrectionPlanOut,
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
def review_aa_chain_finding(
    payload: AaFindingReviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fase 42: tandai temuan DITINJAU / DIABAIKAN (Production Manager saja, catatan wajib)."""
    require_actor_matches(payload.actor_user_id, current_user)
    rv = review_aa_finding(
        db, event_id=payload.event_id, source_batch_id=payload.source_batch_id,
        result_batch_id=payload.result_batch_id, status=payload.status,
        actor_user_id=payload.actor_user_id, note=payload.note)
    db.flush()
    return rv


@router.post("/batches/{batch_id}/correct-number", response_model=BatchNumberCorrectionOut, status_code=201)
def correct_number(
    batch_id: int,
    payload: BatchNumberCorrectionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fase 42: ganti nomor batch (Production Manager saja, alasan wajib, jejak lengkap)."""
    require_actor_matches(payload.actor_user_id, current_user)
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


# ---------------------------------------------- Fase 46: koreksi jenis + cascade
def _plan_out(plan) -> JenisCorrectionPlanOut:
    return JenisCorrectionPlanOut(
        batch_id=plan.batch_id, old_jenis_code=plan.old_jenis_code,
        new_jenis_code=plan.new_jenis_code, ok=plan.ok,
        changes=plan.changes, stops=plan.stops, blockers=plan.blockers,
    )


@router.get("/batches/{batch_id}/jenis-correction/preview", response_model=JenisCorrectionPlanOut)
def preview_jenis_correction(batch_id: int, new_jenis_code: str, db: Session = Depends(get_db)):
    """Rencana koreksi jenis (tanpa menulis): batch yang akan diubah, titik
    henti cascade (Mixing dll.), dan pemblokir (batch sudah dikirim)."""
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    return _plan_out(plan_jenis_correction(db, batch_id=batch_id, new_jenis_code=new_jenis_code))


@router.post("/batches/{batch_id}/correct-jenis", response_model=JenisCorrectionOut, status_code=201)
def correct_jenis_endpoint(
    batch_id: int,
    payload: JenisCorrectionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_actor_matches(payload.actor_user_id, current_user)
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    entry, plan = correct_batch_jenis(
        db, batch_id=batch_id, new_jenis_code=payload.new_jenis_code,
        actor_user_id=payload.actor_user_id, reason=payload.reason,
    )
    db.flush()
    out = JenisCorrectionOut.model_validate(entry)
    out.notice = entry.notice
    out.plan = _plan_out(plan)
    return out


@router.get("/batches/{batch_id}/jenis-history", response_model=list[JenisCorrectionOut])
def get_jenis_history(batch_id: int, db: Session = Depends(get_db)):
    """Koreksi jenis yang mengubah batch ini (sebagai akar atau turunan)."""
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")
    return list_jenis_corrections(db, batch_id=batch_id)
