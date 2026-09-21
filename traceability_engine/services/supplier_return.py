"""Status retur ke supplier: DIKIRIM -> DITERIMA (Fase 38).

Keputusan user (2026-09-21, AskUserQuestion "Fase 38"): retur ke supplier
punya DUA status.

1. **DIKIRIM** = event `SUPPLIER_RETURN` sudah tercatat (dari off-spec saat
   Receiving, Fase 29, atau dari `return_to_supplier()` batch REJECTED,
   Fase 26). Perilaku pencatatan lama TIDAK berubah.
2. **DITERIMA** = ada konfirmasi terpisah "supplier sudah menerima" beserta
   tanggal terima (`SupplierReturnReceipt`, satu per event).

Keputusan yang diambil di sini (CLAUDE.md rule 11) -- `[UNCONFIRMED]`:

- Siapa yang boleh mengonfirmasi TIDAK dibatasi role (user tidak menyebut
  pembatasan; opsi "hanya Production Manager" tidak dipilih). Setiap
  konfirmasi menulis AuditLog (siapa, kapan, tanggal terima).
- Tanggal terima tidak boleh lebih awal dari tanggal event pengembalian.
- Konfirmasi tidak bisa diulang atau dibatalkan (satu per event); salah
  konfirmasi = perlu keputusan terpisah, sengaja belum ada fitur batal.
- Konfirmasi tidak mengubah stok atau status batch; hanya status retur.
- Event yang sudah VOID tidak bisa dikonfirmasi dan tidak dilaporkan.
- Laporan bersifat read-only; retur yang lama berstatus DIKIRIM hanya
  ditampilkan (`days_outstanding`), tanpa ambang/blokir/pengingat.
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
from ..exceptions import InvalidEventStructureError
from ..models import AuditLog, EventBatchLink, ProcessEvent, SupplierReturnReceipt, User

STATUS_SENT = "DIKIRIM"
STATUS_RECEIVED = "DITERIMA"


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


def list_supplier_returns(
    session: Session,
    *,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    as_of: Optional[dt.date] = None,
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
                days_outstanding=(
                    (as_of - ev.event_date).days if receipt is None else None
                ),
            )
        )
    return rows
