"""Disposisi batch REJECTED (Fase 26) -- dikembalikan ke supplier.

Konteks: GENEALOGY.md §8 poin 1 ("QC/MD disposition rules") terbuka sejak Fase
2: apa yang terjadi pada batch REJECTED setelah PIC menandainya secara manual
(`mark_batch_rejected()`, tanpa ambang numerik -- §5.1)?

Terkonfirmasi user (2026-09-19, AskUserQuestion "Fase 26"):

1. **Nasib batch REJECTED = dikembalikan ke supplier.** Bukan scrap, bukan
   rework, bukan HOLD.
2. **Yang memutuskan disposisi = Production Manager** (saat ini Robiah). PIC
   tetap yang menandai REJECTED (perilaku lama tidak berubah); pengembalian
   dicatat oleh PRODUCTION_MANAGER. Tanpa persetujuan orang kedua.
3. **Penegakan = laporan/peringatan saja.** Sistem TIDAK memblokir pemakaian
   batch REJECTED sebagai input event lain; `audit_disposition()` hanya
   melaporkan (pola sama dengan Fase 25).

Yang dibuat:

- `EventType.SUPPLIER_RETURN` (stock OUT, tanpa output batch -- termasuk
  `NO_OUTPUT_EVENT_TYPES`, seperti Delivery). Pengembalian tercatat sebagai
  event genealogi sehingga jejaknya (siapa, kapan, berapa kg, alasan) muncul
  di forward trace / riwayat stok, bukan sekadar edit angka.
- `return_to_supplier()` -- gated ke PRODUCTION_MANAGER
  (`UnauthorizedDispositionError`); batch harus berstatus REJECTED; alasan
  wajib; `quantity` default = seluruh stok, boleh sebagian; menulis AuditLog.
- `audit_disposition()` -- read-only: tiap batch REJECTED beserta status
  disposisinya (PENDING_RETURN / PARTIALLY_RETURNED / RETURNED / NO_STOCK) dan
  event pemakaian SETELAH penolakan (peringatan).

Keputusan yang diambil di sini (CLAUDE.md rule 11):

- Status batch tetap `REJECTED` setelah dikembalikan (qty menjadi 0); tidak
  ada nilai `BatchStatus` baru. Bukti pengembalian = event SUPPLIER_RETURN.
- `[UNCONFIRMED]` batch turunan (hasil sortasi/mixing) mungkin tidak punya
  `supplier_id`; pengembalian TIDAK diblokir untuk kasus itu -- supplier
  dicatat di notes bila ada.
- `[UNCONFIRMED]` apakah off-spec saat Receiving (kolom `off_spec_qty`, Fase 4)
  juga harus otomatis memakai alur ini; saat ini belum -- off-spec tetap hanya
  dicatat, dan batch penuh baru bisa dikembalikan setelah ditandai REJECTED.
- Deteksi "dipakai setelah ditolak" membandingkan `ProcessEvent.created_at`
  dengan waktu AuditLog REJECTED (waktu pencatatan, bukan `event_date`).
  Event inspeksi (QC_TEST, METAL_DETECTION), SUPPLIER_RETURN, dan event VOID
  tidak dihitung.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import (
    AuditAction,
    BatchStatus,
    EventStatus,
    EventType,
    LinkRole,
    UserRole,
)
from ..exceptions import InvalidEventStructureError, UnauthorizedDispositionError
from ..models import AuditLog, Batch, EventBatchLink, ProcessEvent, User
from .events import InputSpec, record_process_event

_NOT_USAGE = frozenset(
    {EventType.QC_TEST, EventType.METAL_DETECTION, EventType.SUPPLIER_RETURN, EventType.ADJUSTMENT}
)


def return_to_supplier(
    session: Session,
    *,
    batch_id: int,
    actor_user_id: int,
    reason: str,
    event_date: dt.date,
    quantity: Optional[Decimal] = None,
    event_time: Optional[dt.time] = None,
) -> ProcessEvent:
    """Catat pengembalian batch REJECTED ke supplier (stock OUT).

    Hanya PRODUCTION_MANAGER; batch harus REJECTED; `reason` wajib;
    `quantity` default = seluruh stok batch (boleh lebih kecil).
    """
    if not reason or not reason.strip():
        raise InvalidEventStructureError("SUPPLIER_RETURN requires a non-empty reason.")

    actor = session.get(User, actor_user_id)
    if actor is None or actor.role != UserRole.PRODUCTION_MANAGER:
        raise UnauthorizedDispositionError(
            f"User {actor_user_id} is not authorized to decide a batch disposition "
            "-- only the PRODUCTION_MANAGER role may (confirmed 2026-09-19)."
        )

    batch = session.get(Batch, batch_id)
    if batch is None:
        raise InvalidEventStructureError(f"Batch {batch_id} does not exist.")
    if batch.status != BatchStatus.REJECTED:
        raise InvalidEventStructureError(
            f"Batch {batch_id} has status {batch.status.value}; only a REJECTED "
            "batch can be returned to the supplier."
        )

    qty = batch.current_quantity if quantity is None else quantity
    if qty <= 0:
        raise InvalidEventStructureError("Return quantity must be positive (batch may have no stock left).")

    payload = {
        "reason": reason.strip(),
        "supplier_id": batch.supplier_id,
        "supplier_name": batch.supplier.name if batch.supplier is not None else None,
    }
    event = record_process_event(
        session,
        event_type=EventType.SUPPLIER_RETURN,
        event_date=event_date,
        event_time=event_time,
        pic_user_id=actor_user_id,
        inputs=[InputSpec(batch_id=batch_id, quantity=qty, unit=batch.unit)],
        outputs=[],
        notes=json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False),
    )
    session.add(
        AuditLog(
            entity_type="Batch",
            entity_id=batch_id,
            action=AuditAction.UPDATE,
            actor_user_id=actor_user_id,
            before_value=f"REJECTED qty={qty + batch.current_quantity}",
            after_value=f"RETURNED_TO_SUPPLIER: {qty} {batch.unit} -- {reason.strip()}",
        )
    )
    session.flush()
    return event


@dataclass
class DispositionRow:
    batch_id: int
    batch_number: Optional[str]
    supplier_id: Optional[int]
    supplier_name: Optional[str]
    rejected_at: Optional[dt.datetime]
    reject_reason: Optional[str]
    quantity_on_hand: Decimal
    returned_quantity: Decimal
    disposition: str  # PENDING_RETURN | PARTIALLY_RETURNED | RETURNED | NO_STOCK
    return_event_ids: list[int] = field(default_factory=list)
    used_after_rejection_event_ids: list[int] = field(default_factory=list)  # peringatan

    def message(self) -> str:
        label = self.batch_number or str(self.batch_id)
        text = {
            "PENDING_RETURN": f"Batch {label} REJECTED, {self.quantity_on_hand} belum dikembalikan ke supplier.",
            "PARTIALLY_RETURNED": (
                f"Batch {label} REJECTED, {self.returned_quantity} sudah dikembalikan, "
                f"sisa {self.quantity_on_hand}."
            ),
            "RETURNED": f"Batch {label} REJECTED, seluruhnya ({self.returned_quantity}) sudah dikembalikan.",
            "NO_STOCK": f"Batch {label} REJECTED, tanpa stok tersisa dan tanpa catatan pengembalian.",
        }[self.disposition]
        if self.used_after_rejection_event_ids:
            ids = ", ".join(f"#{i}" for i in self.used_after_rejection_event_ids)
            text += f" PERINGATAN: dipakai sebagai input event {ids} setelah ditolak."
        return text


def audit_disposition(session: Session, batch_id: Optional[int] = None) -> list[DispositionRow]:
    """Laporan read-only semua batch REJECTED beserta status disposisinya."""
    q = select(Batch).where(Batch.status == BatchStatus.REJECTED).order_by(Batch.batch_id)
    if batch_id is not None:
        q = q.where(Batch.batch_id == batch_id)

    rows: list[DispositionRow] = []
    for batch in session.execute(q).scalars():
        reject_log = session.execute(
            select(AuditLog)
            .where(
                AuditLog.entity_type == "Batch",
                AuditLog.entity_id == batch.batch_id,
                AuditLog.action == AuditAction.REJECTED,
            )
            .order_by(AuditLog.audit_id.desc())
        ).scalars().first()
        rejected_at = reject_log.timestamp if reject_log else None
        reason = None
        if reject_log and reject_log.after_value:
            reason = reject_log.after_value.removeprefix("REJECTED: ")

        links = session.execute(
            select(EventBatchLink, ProcessEvent)
            .join(ProcessEvent, ProcessEvent.event_id == EventBatchLink.event_id)
            .where(
                EventBatchLink.batch_id == batch.batch_id,
                EventBatchLink.role == LinkRole.INPUT,
                ProcessEvent.status == EventStatus.COMPLETED,
            )
            .order_by(ProcessEvent.event_id)
        ).all()

        returned = Decimal("0")
        return_ids: list[int] = []
        used_ids: list[int] = []
        for link, ev in links:
            if ev.event_type == EventType.SUPPLIER_RETURN:
                returned += link.quantity
                return_ids.append(ev.event_id)
            elif (
                ev.event_type not in _NOT_USAGE
                and rejected_at is not None
                and ev.created_at is not None
                and ev.created_at > rejected_at
                and ev.event_id not in used_ids
            ):
                used_ids.append(ev.event_id)

        if returned > 0:
            disposition = "PARTIALLY_RETURNED" if batch.current_quantity > 0 else "RETURNED"
        else:
            disposition = "PENDING_RETURN" if batch.current_quantity > 0 else "NO_STOCK"

        rows.append(
            DispositionRow(
                batch_id=batch.batch_id,
                batch_number=batch.batch_number,
                supplier_id=batch.supplier_id,
                supplier_name=batch.supplier.name if batch.supplier is not None else None,
                rejected_at=rejected_at,
                reject_reason=reason,
                quantity_on_hand=batch.current_quantity,
                returned_quantity=returned,
                disposition=disposition,
                return_event_ids=return_ids,
                used_after_rejection_event_ids=used_ids,
            )
        )
    return rows
