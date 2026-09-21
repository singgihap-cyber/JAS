"""Fase 36 -- laporan audit rantai Hijau: AA (Jenis) yang berubah di tengah rute.

Latar (Fase 33): Jenis (AA) seharusnya TIDAK berubah sepanjang rute; Sortasi
hanya memberi PERINGATAN saat nomor ketikan berbeda dan tetap menyimpan. Laporan
ini menemukan kembali kasus yang lolos, pada data tersimpan.

Aturan (keputusan user 2026-09-21: "AA berubah di rute", laporan saja):

- Untuk tiap event tidak-VOID, tiap pasangan (batch INPUT, batch OUTPUT) yang
  berbeda dan sama-sama ber-AA resmi (01/02) tetapi AA-nya beda = satu temuan.
- MIXING dikecualikan (Fase 32b: Mixing BOLEH mencampur Planifolia dan
  Tahitensis). ADJUSTMENT/SUPPLIER_RETURN tidak punya batch hasil.
- AA legacy 03/04 atau tak terbaca dilewati (Fase 33: dibiarkan apa adanya).
- `hijau_route` = batch INPUT berasal dari rantai RAW_HIJAU (Hijau adalah
  grade BB 00, bukan Jenis; lihat Fase 30). `hijau_only=True` (default di API)
  hanya melaporkan temuan di rute Hijau.
- Read-only, tanpa skema baru, tidak memblokir apa pun (sama seperti Fase 33).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from .. import batch_number as bn
from ..enums import BatchType, EventStatus, EventType, LinkRole
from ..models import Batch, EventBatchLink, ProcessEvent

_SKIPPED_EVENT_TYPES = (EventType.ADJUSTMENT, EventType.MIXING)


@dataclass
class AaChangeFinding:
    event_id: int
    event_type: str
    event_date: dt.date
    source_batch_id: int
    source_batch_number: Optional[str]
    source_jenis_code: str
    source_jenis_label: str
    result_batch_id: int
    result_batch_number: Optional[str]
    result_jenis_code: str
    result_jenis_label: str
    hijau_route: bool

    def message(self) -> str:
        return (
            f"Event {self.event_type} #{self.event_id} ({self.event_date}): batch "
            f"{self.source_batch_number or self.source_batch_id} "
            f"(AA {self.source_jenis_code} = {self.source_jenis_label}) menjadi "
            f"{self.result_batch_number or self.result_batch_id} "
            f"(AA {self.result_jenis_code} = {self.result_jenis_label}). "
            f"Jenis seharusnya tidak berubah sepanjang rute; periksa kemungkinan salah input."
        )


def _hijau_flags(session: Session, batches: dict[int, Batch]) -> dict[int, bool]:
    """batch_id -> apakah batch itu RAW_HIJAU atau turunan (lewat event) dari
    RAW_HIJAU. Satu pass: parent map dari semua event tidak-VOID."""
    parents: dict[int, set[int]] = {}
    rows = (
        session.query(EventBatchLink.event_id, EventBatchLink.batch_id, EventBatchLink.role)
        .join(ProcessEvent, ProcessEvent.event_id == EventBatchLink.event_id)
        .filter(ProcessEvent.status != EventStatus.VOID)
        .all()
    )
    by_event: dict[int, dict[LinkRole, set[int]]] = {}
    for eid, bid, role in rows:
        by_event.setdefault(eid, {LinkRole.INPUT: set(), LinkRole.OUTPUT: set()})[role].add(bid)
    for roles in by_event.values():
        for out in roles[LinkRole.OUTPUT]:
            parents.setdefault(out, set()).update(roles[LinkRole.INPUT] - {out})
    memo: dict[int, bool] = {}

    def walk(bid: int, seen: frozenset[int]) -> bool:
        if bid in memo:
            return memo[bid]
        b = batches.get(bid)
        if b is not None and b.batch_type == BatchType.RAW_HIJAU:
            memo[bid] = True
            return True
        result = False
        for p in parents.get(bid, ()):
            if p not in seen and walk(p, seen | {bid}):
                result = True
                break
        memo[bid] = result
        return result

    return {bid: walk(bid, frozenset()) for bid in batches}


def audit_aa_chain(
    session: Session, batch_id: Optional[int] = None, hijau_only: bool = False
) -> list[AaChangeFinding]:
    """Temuan perubahan AA resmi (01<->02) antara batch INPUT dan OUTPUT sebuah
    event. `batch_id` menyaring temuan yang menyentuh batch itu (sebagai sumber
    atau hasil)."""
    rows = (
        session.query(EventBatchLink.event_id, EventBatchLink.batch_id, EventBatchLink.role)
        .join(ProcessEvent, ProcessEvent.event_id == EventBatchLink.event_id)
        .filter(
            ProcessEvent.status != EventStatus.VOID,
            ProcessEvent.event_type.notin_(_SKIPPED_EVENT_TYPES),
        )
        .all()
    )
    by_event: dict[int, dict[LinkRole, set[int]]] = {}
    for eid, bid, role in rows:
        by_event.setdefault(eid, {LinkRole.INPUT: set(), LinkRole.OUTPUT: set()})[role].add(bid)

    batches = {b.batch_id: b for b in session.query(Batch).all()}
    events = {
        e.event_id: e
        for e in session.query(ProcessEvent).filter(ProcessEvent.event_id.in_(list(by_event))).all()
    } if by_event else {}
    hijau = _hijau_flags(session, batches)

    out: list[AaChangeFinding] = []
    for eid in sorted(by_event):
        ev = events[eid]
        roles = by_event[eid]
        for src_id in sorted(roles[LinkRole.INPUT]):
            src = batches.get(src_id)
            if src is None or src.jenis_code not in bn.JENIS_CODES:
                continue
            for res_id in sorted(roles[LinkRole.OUTPUT]):
                res = batches.get(res_id)
                if (
                    res is None or res_id == src_id
                    or res.jenis_code not in bn.JENIS_CODES
                    or res.jenis_code == src.jenis_code
                ):
                    continue
                if batch_id is not None and batch_id not in (src_id, res_id):
                    continue
                is_hijau = hijau.get(src_id, False)
                if hijau_only and not is_hijau:
                    continue
                out.append(AaChangeFinding(
                    event_id=eid, event_type=ev.event_type.value, event_date=ev.event_date,
                    source_batch_id=src_id, source_batch_number=src.batch_number,
                    source_jenis_code=src.jenis_code,
                    source_jenis_label=bn.JENIS_CODES[src.jenis_code],
                    result_batch_id=res_id, result_batch_number=res.batch_number,
                    result_jenis_code=res.jenis_code,
                    result_jenis_label=bn.JENIS_CODES[res.jenis_code],
                    hijau_route=is_hijau,
                ))
    return out
