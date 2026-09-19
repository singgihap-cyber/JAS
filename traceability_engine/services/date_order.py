"""Validasi urutan tanggal antar-event (Fase 25).

Aturan: untuk satu batch, tanggal sebuah event tidak boleh LEBIH AWAL daripada
tanggal event lain yang sudah tercatat pada batch yang sama (event pembuat
batch, sortasi, QC, dst). Tanggal SAMA diperbolehkan -- banyak tahap terjadi
di hari yang sama. Event ADJUSTMENT (stock opname, bukan bagian genealogi,
GENEALOGY.md §5.2) dan event VOID diabaikan.

Temuan pemicu (Fase 23, poin c): tanggal KW/MD batch `030218-260618-00` = 15/6
padahal batch baru ada lewat sortasi 18/6.

Keputusan (CLAUDE.md rule 11) -- `[UNCONFIRMED]` apakah PT JAS ingin urutan
tanggal yang salah DIBLOKIR atau hanya DILAPORKAN. Data historis nyata memang
memuat urutan terbalik, jadi blokir keras di mana-mana akan menolak entri
riwayat yang sah. Karena itu:

- `audit_date_order()` -- read-only, melaporkan semua pelanggaran pada data
  yang sudah tersimpan (default, tidak mengubah perilaku apa pun).
- `check_event_date_order()` -- pemeriksaan sebelum event direkam.
- `record_process_event(..., strict_date_order=True)` -- opt-in: menolak event
  dengan `EventDateOrderError`. Default `False` = perilaku lama tidak berubah.

Tanpa skema baru; semuanya diturunkan dari ProcessEvent + EventBatchLink.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..enums import EventStatus, EventType
from ..exceptions import EventDateOrderError
from ..models import Batch, EventBatchLink, ProcessEvent


@dataclass
class DateOrderViolation:
    batch_id: int
    batch_number: Optional[str]
    event_id: Optional[int]  # None untuk event yang belum direkam (pra-cek)
    event_type: Optional[str]
    event_date: dt.date
    prior_event_id: int
    prior_event_type: str
    prior_event_date: dt.date
    days_early: int

    def message(self) -> str:
        return (
            f"Batch {self.batch_number or self.batch_id}: tanggal event "
            f"{self.event_date} lebih awal {self.days_early} hari dari event "
            f"{self.prior_event_type} #{self.prior_event_id} "
            f"({self.prior_event_date}) yang sudah tercatat pada batch yang sama."
        )


def _rows(session: Session, batch_ids: Optional[Iterable[int]] = None):
    q = (
        session.query(EventBatchLink.batch_id, ProcessEvent)
        .join(ProcessEvent, ProcessEvent.event_id == EventBatchLink.event_id)
        .filter(
            ProcessEvent.status != EventStatus.VOID,
            ProcessEvent.event_type != EventType.ADJUSTMENT,
        )
    )
    if batch_ids is not None:
        q = q.filter(EventBatchLink.batch_id.in_(list(batch_ids)))
    return q.order_by(EventBatchLink.batch_id, ProcessEvent.event_id).all()


def _batch_numbers(session: Session, ids: set[int]) -> dict[int, Optional[str]]:
    if not ids:
        return {}
    return {
        b.batch_id: b.batch_number
        for b in session.query(Batch).filter(Batch.batch_id.in_(ids)).all()
    }


def audit_date_order(
    session: Session, batch_id: Optional[int] = None
) -> list[DateOrderViolation]:
    """Laporkan event yang tanggalnya lebih awal dari event yang lebih dulu
    TERCATAT (urutan event_id) pada batch yang sama. Satu baris per
    (batch, event); pembanding = event tercatat sebelumnya dengan tanggal
    terlambat."""
    rows = _rows(session, [batch_id] if batch_id is not None else None)
    out: list[DateOrderViolation] = []
    seen: set[tuple[int, int]] = set()
    latest: dict[int, ProcessEvent] = {}
    for bid, ev in rows:
        key = (bid, ev.event_id)
        if key in seen:  # self-loop: link INPUT + OUTPUT pada batch yang sama
            continue
        seen.add(key)
        prior = latest.get(bid)
        if prior is not None and ev.event_date < prior.event_date:
            out.append(
                DateOrderViolation(
                    batch_id=bid, batch_number=None, event_id=ev.event_id,
                    event_type=ev.event_type.value, event_date=ev.event_date,
                    prior_event_id=prior.event_id,
                    prior_event_type=prior.event_type.value,
                    prior_event_date=prior.event_date,
                    days_early=(prior.event_date - ev.event_date).days,
                )
            )
        if prior is None or ev.event_date >= prior.event_date:
            latest[bid] = ev
    nums = _batch_numbers(session, {v.batch_id for v in out})
    for v in out:
        v.batch_number = nums.get(v.batch_id)
    return out


def check_event_date_order(
    session: Session, event_date: dt.date, batch_ids: Iterable[int]
) -> list[DateOrderViolation]:
    """Pra-cek untuk event yang AKAN direkam pada `event_date` menyentuh
    `batch_ids`: kembalikan pelanggaran terhadap event yang sudah ada."""
    ids = set(batch_ids)
    latest: dict[int, ProcessEvent] = {}
    for bid, ev in _rows(session, ids):
        cur = latest.get(bid)
        if cur is None or ev.event_date > cur.event_date:
            latest[bid] = ev
    nums = _batch_numbers(session, ids)
    return [
        DateOrderViolation(
            batch_id=bid, batch_number=nums.get(bid), event_id=None,
            event_type=None, event_date=event_date,
            prior_event_id=prior.event_id,
            prior_event_type=prior.event_type.value,
            prior_event_date=prior.event_date,
            days_early=(prior.event_date - event_date).days,
        )
        for bid, prior in sorted(latest.items())
        if event_date < prior.event_date
    ]


def enforce_event_date_order(
    session: Session, event_date: dt.date, batch_ids: Iterable[int]
) -> None:
    violations = check_event_date_order(session, event_date, batch_ids)
    if violations:
        raise EventDateOrderError("; ".join(v.message() for v in violations))
