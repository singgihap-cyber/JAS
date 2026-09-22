"""Fase 46 -- koreksi Jenis (AA) sebuah batch + cascade ke batch turunan.

Backlog turunan Fase 44/45 ("koreksi AA/jenis/catatan pada event historis").
AA tidak disimpan di event, melainkan di batch (`Batch.jenis_code`, segmen
pertama nomor batch). Fase 42 sudah bisa mengganti nomor SATU batch utuh
tetapi sengaja TIDAK menyentuh batch turunan (keputusan 42b). Fase ini
menambah jalur khusus "ganti jenis saja" yang ikut mengubah turunan.

Keputusan user (2026-09-23, AskUserQuestion "Fase 46"):

1. **Ganti jenis + ikut ubah batch turunan.** Nomor baru disusun otomatis:
   hanya segmen AA yang diganti, segmen lain (grade, supplier, tanggal,
   kode proses) tetap.
2. **Boleh walau batch sudah punya turunan** -- justru itu tujuannya. Syarat
   blokir turunan Fase 44/45 hanya berlaku untuk koreksi catatan, bukan jenis.
   Jenis tidak mengubah stok, jadi tidak ada risiko ledger.
3. **Cascade berhenti di Mixing.** Batch hasil Mixing TIDAK diubah, hanya
   ditandai di rencana (Mixing boleh mencampur jenis, Fase 32b).
4. **Tolak SELURUH koreksi bila ada batch yang sudah dikirim** di antara
   batch yang akan diubah -- label lamanya sudah di tangan customer.

Keputusan yang diambil di sini (`[UNCONFIRMED]`, CLAUDE.md rule 11):

- Hanya Production Manager, alasan wajib (pola Fase 42/44/45).
- Jenis baru hanya kode resmi `01`/`02`; jenis lama boleh kode legacy
  (03/04) -- koreksi legacy -> resmi diizinkan.
- "Sudah dikirim" = status `SHIPPED` ATAU punya event DELIVERY /
  SAMPLE_DELIVERY non-VOID sebagai INPUT (pengiriman sebagian juga dihitung,
  karena barang berlabel lama tetap sudah keluar).
- Selain Mixing, cascade juga berhenti di batch yang: (a) jenisnya sudah
  berbeda dari jenis lama batch akar (mis. Sortasi pernah mengganti AA,
  Fase 32), (b) tidak punya nomor batch, atau (c) punya sumber lain di luar
  rantai ini (mis. hasil Sortasi yang digabung ke batch yang sudah ada) --
  alasan yang sama dengan Mixing: isinya bukan murni dari batch akar.
- Event VOID diabaikan saat menelusuri turunan.
- Pemeriksaan "sudah dikirim" hanya untuk batch yang AKAN diubah; batch di
  balik titik henti tidak diubah, jadi labelnya tidak terpengaruh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import batch_number as bn
from ..enums import AuditAction, BatchStatus, EventStatus, EventType, LinkRole
from ..exceptions import BatchNumberConflictError, InvalidEventStructureError
from ..models import AuditLog, Batch, EventBatchLink, JenisCorrection, ProcessEvent
from .aa_review import _require_manager, correct_batch_number

SHIP_EVENT_TYPES = (EventType.DELIVERY, EventType.SAMPLE_DELIVERY)

JENIS_PRINT_NOTICE = (
    "Ganti nomor lama pada label/dokumen yang sudah tercetak untuk SEMUA batch yang diubah. "
    "Batch yang ditandai 'berhenti' tidak berubah -- periksa manual bila perlu."
)


@dataclass
class JenisPlanRow:
    batch_id: int
    batch_number: Optional[str]
    new_batch_number: Optional[str] = None
    via_event_id: Optional[int] = None
    via_event_type: Optional[str] = None
    note: str = ""


@dataclass
class JenisCorrectionPlan:
    batch_id: int
    old_jenis_code: str
    new_jenis_code: str
    changes: list[JenisPlanRow] = field(default_factory=list)
    stops: list[JenisPlanRow] = field(default_factory=list)
    blockers: list[JenisPlanRow] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blockers


def _new_number(number: str, new_jenis: str) -> str:
    return new_jenis + number[2:]


def _live_events_with_input(session: Session, batch_id: int) -> list[ProcessEvent]:
    return list(session.execute(
        select(ProcessEvent)
        .join(EventBatchLink, EventBatchLink.event_id == ProcessEvent.event_id)
        .where(
            EventBatchLink.batch_id == batch_id,
            EventBatchLink.role == LinkRole.INPUT,
            ProcessEvent.status != EventStatus.VOID,
        )
        .distinct()
        .order_by(ProcessEvent.event_id)
    ).scalars())


def _link_batch_ids(session: Session, event_id: int, role: LinkRole) -> list[int]:
    return list(session.execute(
        select(EventBatchLink.batch_id).where(
            EventBatchLink.event_id == event_id, EventBatchLink.role == role)
    ).scalars())


def _outside_sources(session: Session, batch_id: int, inside: set[int]) -> list[int]:
    """Batch sumber (INPUT event non-VOID yang menghasilkan `batch_id`) di luar `inside`."""
    producing = session.execute(
        select(EventBatchLink.event_id)
        .join(ProcessEvent, ProcessEvent.event_id == EventBatchLink.event_id)
        .where(
            EventBatchLink.batch_id == batch_id,
            EventBatchLink.role == LinkRole.OUTPUT,
            ProcessEvent.status != EventStatus.VOID,
        )
        .distinct()
    ).scalars().all()
    outside: set[int] = set()
    for eid in producing:
        for src in _link_batch_ids(session, eid, LinkRole.INPUT):
            if src != batch_id and src not in inside:
                outside.add(src)
    return sorted(outside)


def _shipped_reason(session: Session, batch: Batch) -> Optional[str]:
    if batch.status == BatchStatus.SHIPPED:
        return "status SHIPPED"
    ev = session.execute(
        select(ProcessEvent)
        .join(EventBatchLink, EventBatchLink.event_id == ProcessEvent.event_id)
        .where(
            EventBatchLink.batch_id == batch.batch_id,
            EventBatchLink.role == LinkRole.INPUT,
            ProcessEvent.status != EventStatus.VOID,
            ProcessEvent.event_type.in_(SHIP_EVENT_TYPES),
        )
        .order_by(ProcessEvent.event_id)
    ).scalars().first()
    if ev is not None:
        return f"sudah dikirim ({ev.event_type.value} #{ev.event_id}, {ev.event_date.isoformat()})"
    return None


def plan_jenis_correction(
    session: Session, *, batch_id: int, new_jenis_code: str
) -> JenisCorrectionPlan:
    """Rencana (tanpa menulis apa pun): batch mana yang akan diubah, di mana
    cascade berhenti, dan apa yang memblokir koreksi."""
    root = session.get(Batch, batch_id)
    if root is None:
        raise InvalidEventStructureError(f"Batch {batch_id} tidak ditemukan.")
    if not root.batch_number or not root.jenis_code:
        raise InvalidEventStructureError(
            f"Batch #{batch_id} belum punya nomor batch; jenis tidak bisa dikoreksi di sini.")
    new_jenis = (new_jenis_code or "").strip()
    if new_jenis not in bn.JENIS_CODES:
        raise InvalidEventStructureError(
            f"Jenis baru {new_jenis!r} tidak valid (hanya "
            f"{', '.join(f'{k}={v}' for k, v in bn.JENIS_CODES.items())}).")
    old_jenis = root.jenis_code
    if new_jenis == old_jenis:
        raise InvalidEventStructureError("Jenis baru sama dengan jenis batch saat ini.")

    plan = JenisCorrectionPlan(batch_id=batch_id, old_jenis_code=old_jenis, new_jenis_code=new_jenis)
    plan.changes.append(JenisPlanRow(
        batch_id=root.batch_id, batch_number=root.batch_number,
        new_batch_number=_new_number(root.batch_number, new_jenis), note="batch akar"))
    inside: set[int] = {root.batch_id}
    visited: set[int] = {root.batch_id}
    queue: list[Batch] = [root]
    while queue:
        cur = queue.pop(0)
        for ev in _live_events_with_input(session, cur.batch_id):
            for out_id in _link_batch_ids(session, ev.event_id, LinkRole.OUTPUT):
                if out_id == cur.batch_id or out_id in visited:
                    continue
                visited.add(out_id)
                child = session.get(Batch, out_id)
                row = JenisPlanRow(
                    batch_id=child.batch_id, batch_number=child.batch_number,
                    via_event_id=ev.event_id, via_event_type=ev.event_type.value)
                if ev.event_type == EventType.MIXING:
                    row.note = "hasil Mixing -- tidak diubah (Mixing boleh campur jenis)"
                    plan.stops.append(row)
                    continue
                if not child.batch_number or not child.jenis_code:
                    row.note = "tanpa nomor batch -- tidak diubah"
                    plan.stops.append(row)
                    continue
                if child.jenis_code != old_jenis:
                    row.note = f"jenis sudah berbeda ({child.jenis_code}) -- tidak diubah"
                    plan.stops.append(row)
                    continue
                outside = _outside_sources(session, child.batch_id, inside)
                if outside:
                    row.note = ("punya sumber lain di luar rantai ("
                                + ", ".join(f"#{i}" for i in outside) + ") -- tidak diubah")
                    plan.stops.append(row)
                    continue
                row.new_batch_number = _new_number(child.batch_number, new_jenis)
                plan.changes.append(row)
                inside.add(child.batch_id)
                queue.append(child)

    for row in plan.changes:
        batch = session.get(Batch, row.batch_id)
        reason = _shipped_reason(session, batch)
        if reason:
            plan.blockers.append(JenisPlanRow(
                batch_id=row.batch_id, batch_number=row.batch_number, note=reason))
        clash = (
            session.query(Batch)
            .filter(Batch.batch_number == row.new_batch_number, Batch.batch_id.notin_(inside))
            .first()
        )
        if clash is not None:
            plan.blockers.append(JenisPlanRow(
                batch_id=row.batch_id, batch_number=row.batch_number,
                new_batch_number=row.new_batch_number,
                note=f"nomor baru {row.new_batch_number} sudah dipakai batch #{clash.batch_id}"))
    return plan


def correct_batch_jenis(
    session: Session, *, batch_id: int, new_jenis_code: str, actor_user_id: int, reason: str,
) -> tuple[JenisCorrection, JenisCorrectionPlan]:
    """Terapkan koreksi jenis + cascade. Ditolak utuh bila rencana punya blocker."""
    _require_manager(session, actor_user_id, "mengoreksi jenis batch")
    if not reason or not reason.strip():
        raise InvalidEventStructureError("Alasan koreksi jenis wajib diisi.")
    plan = plan_jenis_correction(session, batch_id=batch_id, new_jenis_code=new_jenis_code)
    if not plan.ok:
        detail = "; ".join(f"#{b.batch_id} {b.batch_number or ''}: {b.note}" for b in plan.blockers)
        if any("sudah dipakai" in b.note for b in plan.blockers):
            raise BatchNumberConflictError(f"Koreksi jenis ditolak seluruhnya -- {detail}")
        raise InvalidEventStructureError(f"Koreksi jenis ditolak seluruhnya -- {detail}")

    reason = reason.strip()
    entry = JenisCorrection(
        batch_id=batch_id, old_jenis_code=plan.old_jenis_code, new_jenis_code=plan.new_jenis_code,
        changed_batch_ids=",".join(str(r.batch_id) for r in plan.changes),
        stopped_summary="; ".join(f"#{s.batch_id} {s.batch_number or ''}: {s.note}" for s in plan.stops) or None,
        reason=reason, actor_user_id=actor_user_id,
    )
    session.add(entry)
    session.flush()
    for row in plan.changes:
        via = "batch akar" if row.batch_id == batch_id else f"turunan batch #{batch_id}"
        correct_batch_number(
            session, batch_id=row.batch_id, new_batch_number=row.new_batch_number,
            actor_user_id=actor_user_id,
            reason=f"Koreksi jenis #{entry.correction_id} ({via}, "
                   f"{plan.old_jenis_code} -> {plan.new_jenis_code}): {reason}",
        )
    session.add(AuditLog(
        entity_type="JenisCorrection", entity_id=entry.correction_id, action=AuditAction.UPDATE,
        actor_user_id=actor_user_id,
        before_value=f"jenis {plan.old_jenis_code} batch #{batch_id}",
        after_value=(f"jenis {plan.new_jenis_code}; {len(plan.changes)} batch diubah, "
                     f"{len(plan.stops)} titik henti ({reason})"),
    ))
    session.flush()
    entry.notice = JENIS_PRINT_NOTICE  # atribut sementara untuk respons API
    return entry, plan


def list_jenis_corrections(session: Session, *, batch_id: Optional[int] = None) -> list[JenisCorrection]:
    q = session.query(JenisCorrection)
    if batch_id is not None:
        q = q.filter(JenisCorrection.changed_batch_ids.isnot(None))
        rows = [c for c in q.order_by(JenisCorrection.correction_id).all()
                if str(batch_id) in c.changed_batch_ids.split(",")]
        return rows
    return q.order_by(JenisCorrection.correction_id).all()
