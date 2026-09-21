"""Audit perubahan AA sepanjang rute Hijau (Fase 36) -- pembungkus tipis
read-only atas `services.aa_chain`. Tidak menyimpan apa pun dan tidak memblokir."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services.aa_chain import audit_aa_chain
from ..database import get_db
from ..schemas import AaChangeFindingOut

router = APIRouter(tags=["aa-chain"])


@router.get("/audit/aa-chain", response_model=list[AaChangeFindingOut])
def get_aa_chain_audit(
    batch_id: Optional[int] = None, hijau_only: bool = True, db: Session = Depends(get_db)
):
    """Batch hasil yang AA-nya (01/02) berbeda dari batch sumber pada event yang
    sama (kecuali Mixing). `hijau_only=true` (default) = hanya rute Hijau.
    Kosong = tidak ada perubahan AA."""
    return [{**f.__dict__, "message": f.message()} for f in audit_aa_chain(db, batch_id, hijau_only)]
