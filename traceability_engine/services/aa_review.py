"""Fase 42 -- tandai-tinjau temuan audit AA dan koreksi nomor batch.

Keputusan user (2026-09-21):

1. Otorisasi: HANYA Production Manager (tinjau dan koreksi); lainnya 403.
2. Temuan audit AA (Fase 36) berstatus BARU -> DITINJAU / DIABAIKAN / DIKOREKSI.
   Catatan wajib untuk DITINJAU dan DIABAIKAN. DIKOREKSI tidak diisi manual:
   otomatis saat nomor batch yang terlibat diganti.
3. Koreksi nomor = ganti nomor batch in-place, alasan wajib. Nomor lama, nomor
   baru, pelaku, waktu masuk `batch_number_corrections` + AuditLog (Batch,
   UPDATE). Batch/event/silsilah tertaut lewat ID sehingga tidak putus. Nomor
   baru yang sama dengan batch LAIN ditolak (BatchNumberConflictError).
4. (Dikonfirmasi user 2026-09-21) Batch turunan yang SUDAH ada tidak diubah;
   batch turunan berikutnya mengikuti nomor baru (generator membaca komponen
   batch sumber). Hasil koreksi memuat CATATAN agar label/nomor lama yang sudah
   tercetak diganti.
5. (Dikonfirmasi user 2026-09-21) Komponen terurai (AA, grade, kode supplier,
   tanggal, kode proses) mengikuti nomor baru, dan `supplier_id` dicocokkan ke
   supplier terdaftar dengan kode yang sama (dibandingkan sebagai angka, jadi
   `24` = `024`).

`[UNCONFIRMED]` keputusan saya: bila kode supplier pada nomor baru BERBEDA dan
tidak ada supplier terdaftar berkode itu, koreksi ditolak (422) -- daftarkan
supplier dulu. Kode `000` (Mixing) mengosongkan `supplier_id`.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from .. import batch_number as bn
from ..enums import AuditAction, UserRole
from ..exceptions import (
    BatchNumberConflictError, InvalidEventStructureError, UnauthorizedDispositionError,
)
from ..models import (
    AaFindingReview, AuditLog, Batch, BatchNumberCorrection, Supplier, User,
)
from .aa_chain import audit_aa_chain

REVIEW_STATUSES = ("DITINJAU", "DIABAIKAN")

PRINT_NOTICE = (
    "Ganti nomor batch lama pada label/dokumen yang sudah tercetak dengan nomor baru. "
    "Batch turunan yang sudah ada tidak berubah; batch turunan berikutnya memakai nomor baru."
)


def _supplier_for_code(session: Session, code: str) -> Optional[Supplier]:
    """Supplier terdaftar dengan kode numerik yang sama (`24` = `024`)."""
    try:
        target = int(code)
    except ValueError:
        return None
    for sup in session.query(Supplier).all():
        try:
            if int(sup.supplier_code) == target:
                return sup
        except ValueError:
            continue
    return None


def _require_manager(session: Session, actor_user_id: int, what: str) -> User:
    actor = session.get(User, actor_user_id)
    if actor is None or actor.role != UserRole.PRODUCTION_MANAGER:
        raise UnauthorizedDispositionError(
            f"User {actor_user_id} tidak berwenang {what} -- hanya Production Manager "
            "(keputusan user 2026-09-21)."
        )
    return actor


def _upsert_review(session, finding, status, actor_user_id, note) -> AaFindingReview:
    rv = (
        session.query(AaFindingReview)
        .filter_by(event_id=finding.event_id, source_batch_id=finding.source_batch_id,
                   result_batch_id=finding.result_batch_id)
        .one_or_none()
    )
    if rv is None:
        rv = AaFindingReview(
            event_id=finding.event_id, source_batch_id=finding.source_batch_id,
            result_batch_id=finding.result_batch_id, status=status,
            reviewed_by=actor_user_id, note=note, reviewed_at=dt.datetime.now())
        session.add(rv)
    else:
        rv.status, rv.reviewed_by, rv.note = status, actor_user_id, note
        rv.reviewed_at = dt.datetime.now()
    session.flush()
    session.add(AuditLog(
        entity_type="AaFindingReview", entity_id=rv.review_id, action=AuditAction.UPDATE,
        actor_user_id=actor_user_id, before_value=None,
        after_value=f"Temuan AA event #{finding.event_id} batch #{finding.source_batch_id} -> "
                    f"#{finding.result_batch_id}: {status}" + (f" ({note})" if note else ""),
    ))
    session.flush()
    return rv


def review_aa_finding(
    session: Session, *, event_id: int, source_batch_id: int, result_batch_id: int,
    status: str, actor_user_id: int, note: str,
) -> AaFindingReview:
    """Tandai temuan sebagai DITINJAU atau DIABAIKAN (hanya Production Manager,
    catatan wajib). Temuan harus masih ada di audit saat ini."""
    _require_manager(session, actor_user_id, "meninjau temuan audit AA")
    if status not in REVIEW_STATUSES:
        raise InvalidEventStructureError(
            f"Status tinjau {status!r} tidak valid (hanya {', '.join(REVIEW_STATUSES)}; "
            "DIKOREKSI terisi otomatis saat nomor batch diganti)."
        )
    if not note or not note.strip():
        raise InvalidEventStructureError("Catatan wajib diisi saat menandai temuan audit AA.")
    finding = next(
        (f for f in audit_aa_chain(session, hijau_only=False)
         if (f.event_id, f.source_batch_id, f.result_batch_id)
         == (event_id, source_batch_id, result_batch_id)),
        None,
    )
    if finding is None:
        raise InvalidEventStructureError(
            f"Temuan (event {event_id}, batch {source_batch_id} -> {result_batch_id}) tidak ada "
            "di audit AA saat ini."
        )
    return _upsert_review(session, finding, status, actor_user_id, note.strip())


def correct_batch_number(
    session: Session, *, batch_id: int, new_batch_number: str, actor_user_id: int, reason: str,
) -> BatchNumberCorrection:
    """Ganti nomor sebuah batch (hanya Production Manager, alasan wajib)."""
    _require_manager(session, actor_user_id, "mengoreksi nomor batch")
    if not reason or not reason.strip():
        raise InvalidEventStructureError("Alasan koreksi nomor batch wajib diisi.")
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise InvalidEventStructureError(f"Batch {batch_id} tidak ditemukan.")
    new_number = (new_batch_number or "").strip()
    try:
        parts = bn.parse(new_number)
    except ValueError as exc:
        raise InvalidEventStructureError(f"Nomor batch baru tidak valid: {exc}") from exc
    if new_number == batch.batch_number:
        raise InvalidEventStructureError("Nomor batch baru sama dengan nomor saat ini.")
    clash = (
        session.query(Batch)
        .filter(Batch.batch_number == new_number, Batch.batch_id != batch_id)
        .first()
    )
    if clash is not None:
        raise BatchNumberConflictError(
            f"Nomor {new_number} sudah dipakai batch #{clash.batch_id}; koreksi ditolak."
        )

    supplier_note = ""
    new_supplier_id = batch.supplier_id
    old_code = batch.supplier_code
    same_supplier = old_code is not None and old_code.isdigit() and int(old_code) == int(parts.supplier_code)
    if not same_supplier:
        if int(parts.supplier_code) == 0:
            new_supplier_id = None  # 000 = batch Mixing (tanpa supplier)
        else:
            sup = _supplier_for_code(session, parts.supplier_code)
            if sup is None:
                raise InvalidEventStructureError(
                    f"Kode supplier {parts.supplier_code} pada nomor baru belum terdaftar; "
                    "daftarkan supplier terlebih dahulu, lalu ulangi koreksi."
                )
            new_supplier_id = sup.supplier_id
        if new_supplier_id != batch.supplier_id:
            supplier_note = f"; supplier_id {batch.supplier_id} -> {new_supplier_id}"

    affected = [
        f for f in audit_aa_chain(session, hijau_only=False)
        if batch_id in (f.source_batch_id, f.result_batch_id)
    ]
    old_number = batch.batch_number
    batch.batch_number = new_number
    batch.jenis_code = parts.jenis_code
    batch.grade_code = parts.grade_code
    batch.supplier_code = parts.supplier_code
    batch.receiving_date = parts.receiving_date
    batch.process_code = parts.process_code
    batch.supplier_id = new_supplier_id

    entry = BatchNumberCorrection(
        batch_id=batch_id, old_batch_number=old_number, new_batch_number=new_number,
        reason=reason.strip(), actor_user_id=actor_user_id)
    session.add(entry)
    session.add(AuditLog(
        entity_type="Batch", entity_id=batch_id, action=AuditAction.UPDATE,
        actor_user_id=actor_user_id, before_value=f"batch_number {old_number}",
        after_value=f"batch_number {new_number} (koreksi: {reason.strip()}){supplier_note}"))
    session.flush()
    for f in affected:  # temuan yang menyentuh batch ini ditandai DIKOREKSI
        _upsert_review(session, f, "DIKOREKSI", actor_user_id,
                       f"Nomor batch #{batch_id} diganti {old_number} -> {new_number}: {reason.strip()}")
    entry.notice = PRINT_NOTICE  # atribut sementara untuk respons API (bukan kolom)
    return entry


def list_number_history(session: Session, *, batch_id: int) -> list[BatchNumberCorrection]:
    return (
        session.query(BatchNumberCorrection)
        .filter_by(batch_id=batch_id)
        .order_by(BatchNumberCorrection.correction_id)
        .all()
    )
