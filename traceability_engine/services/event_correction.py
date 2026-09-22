"""Fase 44 -- koreksi tanggal & pembatalan (soft-cancel) event historis.

Backlog sejak Fase 42 (PROJECT_STATUS.md "Backlog"): event historis yang
salah dicatat (contoh nyata: MD 15/6 pada batch `030218-260618-00`,
dicatat sebelum sortasi 18/6 yang sebenarnya membuat batch itu ada --
Fase 23/25/28/37) sampai sekarang hanya bisa "dibiarkan sebagai catatan
historis". Fase ini menambahkan mekanisme resminya.

Keputusan user (2026-09-22, AskUserQuestion "Fase 44" / backlog):

1. **Lingkup koreksi = `event_date` SAJA.** Kuantitas, AA/jenis, catatan,
   dan `end_date` Sortasi (disimpan di `notes`, `sortation.py` #8) di luar
   lingkup -- backlog fase mendatang bila diminta.
2. **Pembatalan = soft-cancel dengan jejak** (bukan hard delete): event
   ditandai `EventStatus.VOID` (nilai yang sudah ada di skema tapi belum
   pernah benar-benar diset oleh kode apa pun sebelum fase ini -- hanya
   DIKECUALIKAN secara defensif di `date_order.py`/`aa_chain.py`). Baris
   `ProcessEvent`/`EventBatchLink`/`StockTransaction` TIDAK dihapus.
3. **Hanya event TANPA event/batch turunan setelahnya** (pada salah satu
   batch yang disentuhnya) boleh dikoreksi tanggalnya atau dibatalkan --
   `_downstream_event_ids()`. Event yang sudah memicu proses lanjutan
   (mis. hasil Sortasi yang sudah di-Mixing) ditolak, supaya data turunan
   tidak jadi tidak konsisten. Ini juga membuat pembalikan stok pada
   pembatalan SELALU aman: karena tidak ada event lain yang menyentuh
   batch itu setelahnya, `current_quantity` saat ini persis = keadaan
   sesaat setelah event ini, jadi membalik delta `StockTransaction` event
   ini mengembalikannya tepat ke keadaan sebelum event.
4. **Otorisasi: hanya Production Manager, alasan wajib** -- pola sama
   dengan koreksi nomor batch (Fase 42) dan pembatalan konfirmasi retur
   (Fase 40).

Keputusan yang diambil di sini (CLAUDE.md rule 11):

- `cancel_event()` menjadi jalur KETIGA yang mengubah `Batch.current_quantity`
  secara langsung (setelah `record_process_event()`/`record_adjustment()`,
  lihat `services/stock.py` docstring) -- KHUSUS untuk membalik efek event
  yang dibatalkan. Ledger tetap tidak "ditimpa diam-diam": setiap
  pembalikan menambah baris `StockTransaction` KOMPENSASI (arah
  berlawanan), bukan menghapus/mengedit baris lama, sehingga
  `stock.reconcile_batch()` tetap cocok (cache == jumlah ledger).
- Status batch disesuaikan mengikuti kuantitas hasil pembalikan (ACTIVE
  <-> CONSUMED), pola yang sama dengan aturan auto-CONSUMED di
  `record_process_event()`. Tidak ada nilai `BatchStatus` baru untuk
  "batch dari event yang dibatalkan" -- batch RECEIVING yang eventnya
  dibatalkan tertinggal sebagai batch qty 0 berstatus CONSUMED (tidak
  dihapus, demi audit trail), sama seperti batch REJECTED yang tidak
  dihapus (disposition.py).
- `[UNCONFIRMED]` `forward_trace()`/`backward_trace()` (genealogy.py)
  BELUM menyaring keluar event VOID -- akan tetap menampilkannya sebagai
  riwayat, hanya `status`-nya yang terlihat VOID di respons API
  (`ProcessEventOut.status`). Rendemen (Sortasi/Mixing) DIKECUALIKAN dari
  VOID (lihat perubahan di `services/rendemen.py`), supaya angka rendemen
  tidak diam-diam menghitung event yang sudah dibatalkan.
- Event `ADJUSTMENT` di luar mekanisme ini (jalurnya sendiri,
  `services/adjustment.py`, GENEALOGY.md §5.2).
- Event yang sudah VOID tidak bisa dikoreksi/dibatalkan lagi (tidak ada
  "batalkan pembatalan" -- backlog bila diminta).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import AuditAction, EventStatus, EventType, TransactionDirection, UserRole
from ..exceptions import EventDateOrderError, InvalidEventStructureError, UnauthorizedDispositionError
from ..models import (
    AuditLog, Batch, BatchStatus, EventBatchLink, EventCancellation, EventDateCorrection,
    ProcessEvent, StockTransaction, User,
)

ZERO = Decimal("0")


def _require_manager(session: Session, actor_user_id: int, what: str) -> User:
    actor = session.get(User, actor_user_id)
    if actor is None or actor.role != UserRole.PRODUCTION_MANAGER:
        raise UnauthorizedDispositionError(
            f"User {actor_user_id} tidak berwenang {what} -- hanya Production Manager "
            "(keputusan user 2026-09-22)."
        )
    return actor


def _touched_batch_ids(session: Session, event_id: int) -> set[int]:
    return set(
        session.execute(
            select(EventBatchLink.batch_id).where(EventBatchLink.event_id == event_id)
        ).scalars()
    )


def _downstream_event_ids(session: Session, event: ProcessEvent) -> list[int]:
    """Event LAIN (event_id lebih besar = tercatat belakangan, non-VOID)
    yang menyentuh batch mana pun yang disentuh `event` -- kehadirannya
    berarti `event` sudah punya turunan dan tidak boleh dikoreksi/dibatalkan."""
    batch_ids = _touched_batch_ids(session, event.event_id)
    if not batch_ids:
        return []
    rows = session.execute(
        select(EventBatchLink.event_id)
        .join(ProcessEvent, ProcessEvent.event_id == EventBatchLink.event_id)
        .where(
            EventBatchLink.batch_id.in_(batch_ids),
            ProcessEvent.event_id > event.event_id,
            ProcessEvent.status != EventStatus.VOID,
        )
        .distinct()
    ).scalars().all()
    return sorted(set(rows))


def _latest_conflicting_event(
    session: Session, batch_ids: set[int], exclude_event_id: int
) -> Optional[tuple[int, dt.date, str]]:
    """Event LAIN (non-VOID, bukan ADJUSTMENT) dengan `event_date` PALING
    AKHIR di antara batch-batch ini -- pembanding untuk tanggal baru,
    dengan `exclude_event_id` (event yang sedang dikoreksi) dikeluarkan
    dari perbandingan (lihat module docstring)."""
    if not batch_ids:
        return None
    rows = session.execute(
        select(ProcessEvent)
        .join(EventBatchLink, EventBatchLink.event_id == ProcessEvent.event_id)
        .where(
            EventBatchLink.batch_id.in_(batch_ids),
            ProcessEvent.event_id != exclude_event_id,
            ProcessEvent.status != EventStatus.VOID,
            ProcessEvent.event_type != EventType.ADJUSTMENT,
        )
        .distinct()
    ).scalars().all()
    if not rows:
        return None
    best = max(rows, key=lambda ev: (ev.event_date, ev.event_id))
    return (best.event_id, best.event_date, best.event_type.value)


def _guard_correctable(session: Session, event: ProcessEvent, what: str) -> None:
    if event.status == EventStatus.VOID:
        raise InvalidEventStructureError(f"Event {event.event_id} sudah dibatalkan (VOID); tidak bisa {what}.")
    if event.event_type == EventType.ADJUSTMENT:
        raise InvalidEventStructureError(
            "Event ADJUSTMENT dikoreksi/dibatalkan lewat mekanismenya sendiri (services/adjustment.py), bukan di sini."
        )
    blocking = _downstream_event_ids(session, event)
    if blocking:
        ids = ", ".join(f"#{i}" for i in blocking)
        raise InvalidEventStructureError(
            f"Event {event.event_id} sudah punya event turunan ({ids}) pada batch yang sama -- "
            f"hanya event tanpa turunan yang bisa {what}."
        )


def correct_event_date(
    session: Session,
    *,
    event_id: int,
    new_event_date: dt.date,
    actor_user_id: int,
    reason: str,
) -> EventDateCorrection:
    """Koreksi `event_date` sebuah event historis (hanya Production
    Manager, alasan wajib, hanya event tanpa turunan)."""
    _require_manager(session, actor_user_id, "mengoreksi tanggal event historis")
    if not reason or not reason.strip():
        raise InvalidEventStructureError("Alasan koreksi tanggal event wajib diisi.")
    event = session.get(ProcessEvent, event_id)
    if event is None:
        raise InvalidEventStructureError(f"Event {event_id} tidak ditemukan.")
    _guard_correctable(session, event, "dikoreksi tanggalnya")
    if new_event_date == event.event_date:
        raise InvalidEventStructureError("Tanggal baru sama dengan tanggal event saat ini.")

    batch_ids = _touched_batch_ids(session, event_id)
    conflict = _latest_conflicting_event(session, batch_ids, event_id)
    if conflict is not None and new_event_date < conflict[1]:
        c_id, c_date, c_type = conflict
        raise EventDateOrderError(
            f"Tanggal baru ({new_event_date}) lebih awal dari event {c_type} #{c_id} "
            f"({c_date}) yang sudah tercatat pada batch yang sama."
        )

    old_date = event.event_date
    event.event_date = new_event_date
    entry = EventDateCorrection(
        event_id=event_id, old_event_date=old_date, new_event_date=new_event_date,
        reason=reason.strip(), actor_user_id=actor_user_id,
    )
    session.add(entry)
    session.add(AuditLog(
        entity_type="ProcessEvent", entity_id=event_id, action=AuditAction.UPDATE,
        actor_user_id=actor_user_id,
        before_value=f"event_date {old_date.isoformat()}",
        after_value=f"event_date {new_event_date.isoformat()} (koreksi: {reason.strip()})",
    ))
    session.flush()
    return entry


def cancel_event(
    session: Session, *, event_id: int, actor_user_id: int, reason: str
) -> EventCancellation:
    """Batalkan (soft-cancel) sebuah event historis: status -> VOID, efek
    stok dibalik lewat StockTransaction kompensasi (hanya Production
    Manager, alasan wajib, hanya event tanpa turunan)."""
    _require_manager(session, actor_user_id, "membatalkan event historis")
    if not reason or not reason.strip():
        raise InvalidEventStructureError("Alasan pembatalan event wajib diisi.")
    event = session.get(ProcessEvent, event_id)
    if event is None:
        raise InvalidEventStructureError(f"Event {event_id} tidak ditemukan.")
    _guard_correctable(session, event, "dibatalkan")

    txns = session.execute(
        select(StockTransaction).where(StockTransaction.event_id == event_id)
    ).scalars().all()
    touched: dict[int, Batch] = {}
    for txn in txns:
        batch = session.get(Batch, txn.batch_id)
        if batch is None:
            continue
        reversed_direction = (
            TransactionDirection.OUT if txn.direction == TransactionDirection.IN else TransactionDirection.IN
        )
        if txn.direction == TransactionDirection.IN:
            batch.current_quantity -= txn.quantity
        else:
            batch.current_quantity += txn.quantity
        session.add(StockTransaction(
            batch_id=batch.batch_id, event_id=event_id, direction=reversed_direction,
            quantity=txn.quantity, balance_after=batch.current_quantity, is_sample=txn.is_sample,
        ))
        touched[batch.batch_id] = batch

    for batch in touched.values():
        if batch.status not in (BatchStatus.ACTIVE, BatchStatus.CONSUMED):
            continue  # REJECTED/SUPERSEDED/SHIPPED -- keputusan manual, jangan diubah otomatis
        if batch.current_quantity <= ZERO:
            batch.status = BatchStatus.CONSUMED
        else:
            batch.status = BatchStatus.ACTIVE

    old_status = event.status
    event.status = EventStatus.VOID
    entry = EventCancellation(
        event_id=event_id, event_type=event.event_type.value,
        reason=reason.strip(), actor_user_id=actor_user_id,
    )
    session.add(entry)
    session.add(AuditLog(
        entity_type="ProcessEvent", entity_id=event_id, action=AuditAction.VOID,
        actor_user_id=actor_user_id,
        before_value=f"{event.event_type.value} {old_status.value} {event.event_date.isoformat()}",
        after_value=f"VOID (dibatalkan: {reason.strip()})",
    ))
    session.flush()
    return entry


@dataclass
class CorrectableEventRow:
    event_id: int
    event_type: str
    event_date: dt.date
    status: str
    batch_ids: list[int] = field(default_factory=list)
    batch_numbers: list[Optional[str]] = field(default_factory=list)


def list_correctable_events(
    session: Session, *, batch_id: Optional[int] = None, limit: int = 200
) -> list[CorrectableEventRow]:
    """Event yang MASIH BOLEH dikoreksi tanggalnya / dibatalkan -- tidak
    ada event/batch turunan setelahnya. Terbaru (event_id) dulu."""
    stmt = (
        select(ProcessEvent)
        .where(ProcessEvent.status == EventStatus.COMPLETED, ProcessEvent.event_type != EventType.ADJUSTMENT)
        .order_by(ProcessEvent.event_id.desc())
    )
    if batch_id is not None:
        ev_ids = session.execute(
            select(EventBatchLink.event_id).where(EventBatchLink.batch_id == batch_id).distinct()
        ).scalars().all()
        stmt = stmt.where(ProcessEvent.event_id.in_(ev_ids))
    stmt = stmt.limit(limit)

    rows: list[CorrectableEventRow] = []
    for event in session.execute(stmt).scalars():
        if _downstream_event_ids(session, event):
            continue
        ids = sorted(_touched_batch_ids(session, event.event_id))
        batches = {b.batch_id: b for b in session.query(Batch).filter(Batch.batch_id.in_(ids)).all()}
        rows.append(CorrectableEventRow(
            event_id=event.event_id, event_type=event.event_type.value,
            event_date=event.event_date, status=event.status.value,
            batch_ids=ids, batch_numbers=[batches[i].batch_number if i in batches else None for i in ids],
        ))
    return rows


@dataclass
class EventHistoryEntry:
    kind: str  # DATE_CORRECTION | CANCELLATION
    occurred_at: dt.datetime
    actor_user_id: int
    reason: str
    detail: str


def list_event_correction_history(session: Session, *, event_id: int) -> list[EventHistoryEntry]:
    """Gabungan riwayat koreksi tanggal + pembatalan satu event, terlama dulu."""
    entries: list[EventHistoryEntry] = []
    for c in session.execute(
        select(EventDateCorrection).where(EventDateCorrection.event_id == event_id)
        .order_by(EventDateCorrection.correction_id)
    ).scalars():
        entries.append(EventHistoryEntry(
            kind="DATE_CORRECTION", occurred_at=c.corrected_at, actor_user_id=c.actor_user_id,
            reason=c.reason, detail=f"{c.old_event_date.isoformat()} -> {c.new_event_date.isoformat()}",
        ))
    for c in session.execute(
        select(EventCancellation).where(EventCancellation.event_id == event_id)
        .order_by(EventCancellation.cancellation_id)
    ).scalars():
        entries.append(EventHistoryEntry(
            kind="CANCELLATION", occurred_at=c.cancelled_at, actor_user_id=c.actor_user_id,
            reason=c.reason, detail=f"{c.event_type} dibatalkan (VOID)",
        ))
    entries.sort(key=lambda e: e.occurred_at)
    return entries
