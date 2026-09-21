"""Status retur ke supplier: DIKIRIM -> DITERIMA (Fase 38).

Keputusan user (2026-09-21, AskUserQuestion "Fase 38"): retur ke supplier
punya DUA status.

1. **DIKIRIM** = event `SUPPLIER_RETURN` sudah tercatat (dari off-spec saat
   Receiving, Fase 29, atau dari `return_to_supplier()` batch REJECTED,
   Fase 26). Perilaku pencatatan lama TIDAK berubah.
2. **DITERIMA** = ada konfirmasi terpisah "supplier sudah menerima" beserta
   tanggal terima (`SupplierReturnReceipt`, satu per event).

Keputusan yang diambil di sini (CLAUDE.md rule 11) -- `[UNCONFIRMED]`:

- (Fase 38, DIGANTI Fase 40: kini dibatasi Production Manager / PIC Receiving.)
  Setiap konfirmasi menulis AuditLog (siapa, kapan, tanggal terima).
- Tanggal terima tidak boleh lebih awal dari tanggal event pengembalian.
- Satu konfirmasi aktif per event (Fase 38); pembatalan ditambahkan Fase 40.
- Konfirmasi tidak mengubah stok atau status batch; hanya status retur.
- Event yang sudah VOID tidak bisa dikonfirmasi dan tidak dilaporkan.
- Laporan bersifat read-only.

**Fase 40 -- role, pembatalan, riwayat.** Keputusan user (2026-09-21) menutup
pertanyaan terbuka Fase 38/39:
1. Konfirmasi "diterima" HANYA boleh oleh Production Manager ATAU "PIC
   Receiving" = user yang mencatat event RECEIVING batch retur itu
   (`ProcessEvent.pic_user_id`; user memilih ini, bukan role baru). Lainnya
   -> `UnauthorizedDispositionError` (HTTP 403).
   `[UNCONFIRMED]` retur dari batch turunan tanpa event RECEIVING (mis. hasil
   sortasi) tidak punya PIC Receiving -> hanya Production Manager.
2. Konfirmasi BISA dibatalkan, HANYA oleh Production Manager, alasan wajib;
   status kembali DIKIRIM dan pengingat dihitung lagi dari tanggal kirim.
3. Riwayat disimpan (`SupplierReturnHistory`: CONFIRMED/CANCELLED). Status
   saat ini tetap = ada/tidaknya `SupplierReturnReceipt`; pembatalan menghapus
   baris receipt itu, sehingga konfirmasi ulang bisa dilakukan (UNIQUE aman).
   Konfirmasi Fase 38 yang belum punya riwayat di-snapshot ke riwayat saat
   dibatalkan. AuditLog juga mencatat pembatalan.

**Fase 39 -- pengingat.** Keputusan user (2026-09-21): retur berstatus DIKIRIM
diingatkan mulai `REMINDER_AFTER_DAYS` = 3 hari sejak tanggal kirim dan terus
diingatkan sampai dikonfirmasi DITERIMA. Bentuk: banner Dashboard, penanda di
tabel retur, dan `GET /api/supplier-returns/reminders`. HANYA tampilan: tidak
memblokir apa pun, tidak ada tingkat kuning/merah, tidak ada kirim pesan keluar
(server tidak punya kanal email/WhatsApp). Hari dihitung `as_of - event_date`;
"terlambat" = hari >= 3 (retur dikirim 18/9 mulai diingatkan 21/9).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import AuditAction, EventStatus, EventType, LinkRole
from ..enums import UserRole
from ..exceptions import InvalidEventStructureError, UnauthorizedDispositionError
from ..models import (
    AuditLog, EventBatchLink, ProcessEvent, SupplierReturnHistory, SupplierReturnReceipt, User,
)

STATUS_SENT = "DIKIRIM"
STATUS_RECEIVED = "DITERIMA"
REMINDER_AFTER_DAYS = 3  # Fase 39 -- keputusan user 2026-09-21


def _return_batch_id(session: Session, event_id: int) -> Optional[int]:
    link = session.execute(
        select(EventBatchLink).where(
            EventBatchLink.event_id == event_id, EventBatchLink.role == LinkRole.INPUT
        )
    ).scalars().first()
    return link.batch_id if link is not None else None


def can_confirm_return(session: Session, *, event_id: int, user_id: int) -> bool:
    """Production Manager, atau user yang mencatat RECEIVING batch retur itu."""
    user = session.get(User, user_id)
    if user is None:
        return False
    if user.role == UserRole.PRODUCTION_MANAGER:
        return True
    batch_id = _return_batch_id(session, event_id)
    if batch_id is None:
        return False
    recorder = session.execute(
        select(ProcessEvent.event_id)
        .join(EventBatchLink, EventBatchLink.event_id == ProcessEvent.event_id)
        .where(
            EventBatchLink.batch_id == batch_id,
            EventBatchLink.role == LinkRole.OUTPUT,
            ProcessEvent.event_type == EventType.RECEIVING,
            ProcessEvent.status == EventStatus.COMPLETED,
            ProcessEvent.pic_user_id == user_id,
        )
    ).first()
    return recorder is not None


def confirm_return_received(
    session: Session,
    *,
    event_id: int,
    received_date: dt.date,
    actor_user_id: int,
    note: Optional[str] = None,
) -> SupplierReturnReceipt:
    """Catat bahwa supplier sudah menerima retur `event_id` (SUPPLIER_RETURN)."""
    event = session.get(ProcessEvent, event_id)
    if event is None or event.event_type != EventType.SUPPLIER_RETURN:
        raise InvalidEventStructureError(
            f"Event {event_id} bukan event pengembalian ke supplier (SUPPLIER_RETURN)."
        )
    if event.status != EventStatus.COMPLETED:
        raise InvalidEventStructureError(f"Event {event_id} berstatus {event.status.value}.")
    if session.get(User, actor_user_id) is None:
        raise InvalidEventStructureError(f"User {actor_user_id} tidak ditemukan.")
    if not can_confirm_return(session, event_id=event_id, user_id=actor_user_id):
        raise UnauthorizedDispositionError(
            f"User {actor_user_id} tidak berwenang mengonfirmasi retur diterima supplier "
            "-- hanya Production Manager atau PIC yang mencatat Receiving batch retur ini "
            "(keputusan user 2026-09-21)."
        )
    if received_date < event.event_date:
        raise InvalidEventStructureError(
            f"Tanggal diterima supplier ({received_date}) tidak boleh lebih awal dari "
            f"tanggal pengembalian ({event.event_date})."
        )
    existing = session.execute(
        select(SupplierReturnReceipt).where(SupplierReturnReceipt.return_event_id == event_id)
    ).scalars().first()
    if existing is not None:
        raise InvalidEventStructureError(
            f"Retur event {event_id} sudah dikonfirmasi diterima supplier pada "
            f"{existing.received_date}."
        )

    receipt = SupplierReturnReceipt(
        return_event_id=event_id,
        received_date=received_date,
        confirmed_by=actor_user_id,
        note=note.strip() if note and note.strip() else None,
    )
    session.add(receipt)
    session.add(SupplierReturnHistory(
        return_event_id=event_id, action="CONFIRMED", received_date=received_date,
        actor_user_id=actor_user_id, note=receipt.note,
    ))
    session.add(
        AuditLog(
            entity_type="ProcessEvent",
            entity_id=event_id,
            action=AuditAction.UPDATE,
            actor_user_id=actor_user_id,
            before_value=f"SUPPLIER_RETURN {STATUS_SENT}",
            after_value=f"SUPPLIER_RETURN {STATUS_RECEIVED}: {received_date.isoformat()}",
        )
    )
    session.flush()
    return receipt


def cancel_return_confirmation(
    session: Session, *, event_id: int, actor_user_id: int, reason: str
) -> SupplierReturnHistory:
    """Batalkan konfirmasi "diterima" (Fase 40): status kembali DIKIRIM.

    Hanya Production Manager; alasan wajib; hanya bila status saat ini DITERIMA.
    Riwayat dipertahankan (CONFIRMED lama + CANCELLED baru)."""
    if not reason or not reason.strip():
        raise InvalidEventStructureError("Pembatalan konfirmasi retur wajib mencantumkan alasan.")
    actor = session.get(User, actor_user_id)
    if actor is None or actor.role != UserRole.PRODUCTION_MANAGER:
        raise UnauthorizedDispositionError(
            f"User {actor_user_id} tidak berwenang membatalkan konfirmasi retur "
            "-- hanya Production Manager (keputusan user 2026-09-21)."
        )
    receipt = session.execute(
        select(SupplierReturnReceipt).where(SupplierReturnReceipt.return_event_id == event_id)
    ).scalars().first()
    if receipt is None:
        raise InvalidEventStructureError(
            f"Retur event {event_id} tidak berstatus {STATUS_RECEIVED}; tidak ada konfirmasi untuk dibatalkan."
        )
    # Konfirmasi Fase 38 belum punya riwayat: snapshot dulu agar jejak utuh.
    has_confirm = session.execute(
        select(SupplierReturnHistory.history_id).where(
            SupplierReturnHistory.return_event_id == event_id,
            SupplierReturnHistory.action == "CONFIRMED",
            SupplierReturnHistory.actor_user_id == receipt.confirmed_by,
            SupplierReturnHistory.received_date == receipt.received_date,
        )
    ).first()
    if has_confirm is None:
        session.add(SupplierReturnHistory(
            return_event_id=event_id, action="CONFIRMED", received_date=receipt.received_date,
            actor_user_id=receipt.confirmed_by, note=receipt.note,
            occurred_at=receipt.confirmed_at,
        ))
    entry = SupplierReturnHistory(
        return_event_id=event_id, action="CANCELLED", received_date=receipt.received_date,
        actor_user_id=actor_user_id, note=reason.strip(),
    )
    session.add(entry)
    session.add(AuditLog(
        entity_type="ProcessEvent", entity_id=event_id, action=AuditAction.UPDATE,
        actor_user_id=actor_user_id,
        before_value=f"SUPPLIER_RETURN {STATUS_RECEIVED}: {receipt.received_date.isoformat()}",
        after_value=f"SUPPLIER_RETURN {STATUS_SENT} (konfirmasi dibatalkan: {reason.strip()})",
    ))
    session.delete(receipt)
    session.flush()
    return entry


def list_return_history(session: Session, *, event_id: int) -> list[SupplierReturnHistory]:
    """Riwayat konfirmasi/pembatalan satu retur, terlama dulu."""
    return list(session.execute(
        select(SupplierReturnHistory)
        .where(SupplierReturnHistory.return_event_id == event_id)
        .order_by(SupplierReturnHistory.history_id)
    ).scalars())


@dataclass
class SupplierReturnRow:
    event_id: int
    event_date: dt.date
    batch_id: Optional[int]
    batch_number: Optional[str]
    supplier_id: Optional[int]
    supplier_name: Optional[str]
    quantity: Decimal
    unit: str
    source: str  # RECEIVING_OFF_SPEC | REJECTED_BATCH
    reason: Optional[str]
    status: str  # DIKIRIM | DITERIMA
    received_date: Optional[dt.date] = None
    confirmed_by: Optional[int] = None
    note: Optional[str] = None
    days_outstanding: Optional[int] = None  # hanya DIKIRIM: hari sejak dikirim s.d. `as_of`
    overdue: bool = False  # Fase 39: DIKIRIM dan days_outstanding >= REMINDER_AFTER_DAYS


def list_supplier_returns(
    session: Session,
    *,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    as_of: Optional[dt.date] = None,
    overdue: Optional[bool] = None,
) -> list[SupplierReturnRow]:
    """Semua retur ke supplier (event SUPPLIER_RETURN non-VOID), terbaru dulu."""
    if status is not None and status not in (STATUS_SENT, STATUS_RECEIVED):
        raise ValueError(f"status harus {STATUS_SENT} atau {STATUS_RECEIVED}.")
    as_of = as_of or dt.date.today()

    events = session.execute(
        select(ProcessEvent)
        .where(
            ProcessEvent.event_type == EventType.SUPPLIER_RETURN,
            ProcessEvent.status == EventStatus.COMPLETED,
        )
        .order_by(ProcessEvent.event_id.desc())
    ).scalars().all()
    receipts = {
        r.return_event_id: r
        for r in session.execute(select(SupplierReturnReceipt)).scalars()
    }

    rows: list[SupplierReturnRow] = []
    for ev in events:
        link = session.execute(
            select(EventBatchLink).where(
                EventBatchLink.event_id == ev.event_id, EventBatchLink.role == LinkRole.INPUT
            )
        ).scalars().first()
        batch = link.batch if link is not None else None
        try:
            notes = json.loads(ev.notes) if ev.notes else {}
        except ValueError:
            notes = {}
        sup_id = notes.get("supplier_id") or (batch.supplier_id if batch else None)
        sup_name = notes.get("supplier_name")
        if sup_name is None and batch is not None and batch.supplier is not None:
            sup_name = batch.supplier.name
        receipt = receipts.get(ev.event_id)
        row_status = STATUS_RECEIVED if receipt is not None else STATUS_SENT
        if status is not None and row_status != status:
            continue
        if supplier_id is not None and sup_id != supplier_id:
            continue
        if batch_id is not None and (batch is None or batch.batch_id != batch_id):
            continue
        days = (as_of - ev.event_date).days if receipt is None else None
        is_overdue = days is not None and days >= REMINDER_AFTER_DAYS
        if overdue is not None and is_overdue != overdue:
            continue
        rows.append(
            SupplierReturnRow(
                event_id=ev.event_id,
                event_date=ev.event_date,
                batch_id=batch.batch_id if batch else None,
                batch_number=batch.batch_number if batch else None,
                supplier_id=sup_id,
                supplier_name=sup_name,
                quantity=link.quantity if link is not None else Decimal("0"),
                unit=link.unit if link is not None else "kg",
                source=notes.get("source") or "REJECTED_BATCH",
                reason=notes.get("reason"),
                status=row_status,
                received_date=receipt.received_date if receipt else None,
                confirmed_by=receipt.confirmed_by if receipt else None,
                note=receipt.note if receipt else None,
                days_outstanding=days,
                overdue=is_overdue,
            )
        )
    return rows


@dataclass
class SupplierReturnReminders:
    threshold_days: int
    count: int
    oldest_days: Optional[int]
    total_quantity: Decimal
    message: str
    items: list[SupplierReturnRow]  # tertua dulu


def supplier_return_reminders(
    session: Session, *, as_of: Optional[dt.date] = None
) -> SupplierReturnReminders:
    """Retur DIKIRIM yang sudah >= REMINDER_AFTER_DAYS hari belum dikonfirmasi
    diterima supplier (Fase 39). Read-only; kosong = tidak ada yang perlu diingatkan."""
    items = sorted(
        list_supplier_returns(session, status=STATUS_SENT, overdue=True, as_of=as_of),
        key=lambda r: (-(r.days_outstanding or 0), r.event_id),
    )
    total = sum((r.quantity for r in items), Decimal("0"))
    oldest = items[0].days_outstanding if items else None
    if items:
        names = sorted({r.supplier_name for r in items if r.supplier_name})
        message = (
            f"{len(items)} retur belum dikonfirmasi diterima supplier "
            f"(tertua {oldest} hari" + (f", {', '.join(names)}" if names else "") + ")."
        )
    else:
        message = "Tidak ada retur yang perlu diingatkan."
    return SupplierReturnReminders(
        threshold_days=REMINDER_AFTER_DAYS, count=len(items), oldest_days=oldest,
        total_quantity=total, message=message, items=items,
    )
