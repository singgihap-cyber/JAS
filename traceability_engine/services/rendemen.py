"""Fase 24 -- RENDEMEN Sortasi derived from genealogy.

Source: sheet `SORT` of `PROSES HIJAU 2026.xlsx`, column `RENDEMEN`
(finding (e) of `23_VALIDASI_SHEET_HIJAU.md`). The sheet's figure is a
**ratio, not a percent**: kg of raw (green) material received per kg of
sorted output --

    RENDEMEN = berat hijau PPH / TOTAL (KG) sortasi
             = 77.74 / 13.555 = 5.735   (sheet shows 5.74)

The genealogy already holds both numbers, so nothing new is stored:
the numerator is walked backward from the sortation's input batch to the
RECEIVING event(s) that created its ancestors; the denominator is the sum of
the sortation's OUTPUT links (the sheet's `TOTAL (KG)`).

Decisions (CLAUDE.md rule 11: document ambiguity instead of guessing):

1. **Numerator = received net weight (PPH `NETTO`), before Lepas Tangkai.**
   Matches the sheet (77.74, not the 75.44 left after stem removal).
2. **Denominator = sum of the sortation's OUTPUT quantities** (the sheet's
   TOTAL). In every real row TOTAL == input weight (shrinkage 0), so the
   choice does not matter for the known data; if a sortation ever loses
   weight, `[UNCONFIRMED]` whether PT JAS wants input or output as the base.
   The input weight and shrinkage are returned too so a report can show both.
3. **Proportional attribution.** A batch is not always consumed whole and
   not always from a single ancestor (partial re-sortation, Mixing of up to
   33 sources). Raw weight is therefore attributed by mass fraction: when an
   event consumes `q` of a batch that had `h` on hand, it takes `q/h` of the
   raw weight still remaining in that batch; that amount is split across the
   event's outputs in proportion to their quantities. For the real
   single-chain Hijau case this collapses to "all of the received weight".
   In-place events (QC, MD, Stem Removal, Blanching, Curing, Airdrying ...)
   have an OUTPUT link back to the same batch and consume nothing, so they
   never move raw weight -- they only change `h`, which is why weight loss
   during curing raises the rendemen ratio, as it should.
4. **Unknown lineage is reported, not guessed.** A batch with no creating
   event, or one whose creating event is not RECEIVING and has no traceable
   inputs, makes the numerator unknown: `raw_weight` and `rendemen` are
   `None` and `complete` is `False`.
5. Rendemen is quantised to 4 decimals (the sheet rounds to 2 and its own
   helper column shows 5.7351..., i.e. the ratio is exact, the 2-dp display
   is the sheet's rounding). `yield_percent` (= 100 / rendemen) is provided
   as a convenience; it is plain arithmetic on the same numbers.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import EventType, LinkRole, TransactionDirection
from ..models import Batch, EventBatchLink, ProcessEvent, StockTransaction

ZERO = Decimal("0")
_RATIO_Q = Decimal("0.0001")


@dataclass
class SortationOutput:
    batch_id: int
    batch_number: Optional[str]
    grade_code: Optional[str]
    quantity: Decimal


@dataclass
class SortationRendemen:
    event_id: int
    event_date: dt.date
    input_batch_id: int
    input_batch_number: Optional[str]
    input_quantity: Decimal  # SORT "BERAT (KG)" (weight sorted)
    output_quantity: Decimal  # SORT "TOTAL (KG)"
    shrinkage_qty: Decimal
    raw_weight: Optional[Decimal]  # SORT "berat hijau" (None = lineage unknown)
    rendemen: Optional[Decimal]  # raw_weight / output_quantity
    yield_percent: Optional[Decimal]  # output_quantity / raw_weight * 100
    complete: bool
    outputs: list[SortationOutput] = field(default_factory=list)


class _Attribution:
    """Memoised raw-weight bookkeeping for one call tree (one Session)."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._pool: dict[int, Optional[Decimal]] = {}
        self._taken: dict[tuple[int, int], Optional[Decimal]] = {}

    # -- helpers -----------------------------------------------------------
    def _links(self, event_id: int, role: LinkRole) -> list[EventBatchLink]:
        return list(self.session.execute(
            select(EventBatchLink)
            .where(EventBatchLink.event_id == event_id, EventBatchLink.role == role)
            .order_by(EventBatchLink.link_id)
        ).scalars())

    def _on_hand_before(self, event_id: int, batch_id: int, qty: Decimal) -> Decimal:
        """On-hand of `batch_id` just before `event_id` debited `qty`,
        recovered from the ledger row the engine wrote for that debit."""
        bal = self.session.execute(
            select(StockTransaction.balance_after).where(
                StockTransaction.event_id == event_id,
                StockTransaction.batch_id == batch_id,
                StockTransaction.direction == TransactionDirection.OUT,
            )
        ).scalars().first()
        return qty if bal is None else bal + qty

    def _consuming_events(self, batch_id: int) -> list[int]:
        """Events (oldest first) that take material out of `batch_id`
        without putting it back into the same batch (i.e. not in-place)."""
        in_ids = self.session.execute(
            select(EventBatchLink.event_id).where(
                EventBatchLink.batch_id == batch_id, EventBatchLink.role == LinkRole.INPUT
            )
        ).scalars().all()
        out_ids = set(self.session.execute(
            select(EventBatchLink.event_id).where(
                EventBatchLink.batch_id == batch_id, EventBatchLink.role == LinkRole.OUTPUT
            )
        ).scalars().all())
        return sorted({e for e in in_ids if e not in out_ids})

    # -- core --------------------------------------------------------------
    def pool(self, batch_id: int) -> Optional[Decimal]:
        """Total raw weight that ever flowed into `batch_id` (None=unknown)."""
        if batch_id in self._pool:
            return self._pool[batch_id]
        self._pool[batch_id] = None  # cycle guard
        batch = self.session.get(Batch, batch_id)
        result: Optional[Decimal] = None
        if batch is not None and batch.created_from_event_id is not None:
            event = self.session.get(ProcessEvent, batch.created_from_event_id)
            outs = self._links(event.event_id, LinkRole.OUTPUT)
            mine = next((o.quantity for o in outs if o.batch_id == batch_id), None)
            if event.event_type == EventType.RECEIVING:
                result = mine
            else:
                total_out = sum((o.quantity for o in outs), ZERO)
                inputs = [i for i in self._links(event.event_id, LinkRole.INPUT)
                          if i.batch_id != batch_id]
                if mine is not None and total_out > ZERO and inputs:
                    share = mine / total_out
                    parts = [self.taken(event.event_id, i.batch_id) for i in inputs]
                    if all(p is not None for p in parts):
                        result = sum(parts, ZERO) * share
        self._pool[batch_id] = result
        return result

    def taken(self, event_id: int, batch_id: int) -> Optional[Decimal]:
        """Raw weight carried away from `batch_id` by `event_id`."""
        key = (event_id, batch_id)
        if key in self._taken:
            return self._taken[key]
        self._taken[key] = None
        pool = self.pool(batch_id)
        result: Optional[Decimal] = None
        if pool is not None:
            remaining = pool
            for eid in self._consuming_events(batch_id):
                q = next((l.quantity for l in self._links(eid, LinkRole.INPUT)
                          if l.batch_id == batch_id), ZERO)
                h = self._on_hand_before(eid, batch_id, q)
                frac = ZERO if h <= ZERO else min(q / h, Decimal(1))
                part = remaining * frac
                self._taken[(eid, batch_id)] = part
                remaining -= part
                if eid == event_id:
                    break
            result = self._taken.get(key)
        self._taken[key] = result
        return result


def sortation_rendemen(session: Session, event_id: int,
                       _attr: Optional[_Attribution] = None) -> SortationRendemen:
    event = session.get(ProcessEvent, event_id)
    if event is None:
        raise ValueError(f"Event {event_id} does not exist.")
    if event.event_type != EventType.SORTATION:
        raise ValueError(f"Event {event_id} is {event.event_type.value}, not SORTATION.")

    attr = _attr or _Attribution(session)
    inputs = attr._links(event_id, LinkRole.INPUT)
    outputs = attr._links(event_id, LinkRole.OUTPUT)
    src = inputs[0]  # Sortation is ONE->MANY (services/sortation.py)
    src_batch = session.get(Batch, src.batch_id)

    out_total = sum((o.quantity for o in outputs), ZERO)
    raw = attr.taken(event_id, src.batch_id)
    rendemen = yield_pct = None
    if raw is not None and out_total > ZERO and raw > ZERO:
        rendemen = (raw / out_total).quantize(_RATIO_Q, ROUND_HALF_UP)
        yield_pct = (out_total / raw * 100).quantize(_RATIO_Q, ROUND_HALF_UP)

    out_rows = []
    for o in outputs:
        b = session.get(Batch, o.batch_id)
        out_rows.append(SortationOutput(o.batch_id, b.batch_number, b.grade_code, o.quantity))
    return SortationRendemen(
        event_id=event_id,
        event_date=event.event_date,
        input_batch_id=src.batch_id,
        input_batch_number=src_batch.batch_number,
        input_quantity=src.quantity,
        output_quantity=out_total,
        shrinkage_qty=event.shrinkage_qty,
        raw_weight=None if raw is None else raw.quantize(Decimal("0.001"), ROUND_HALF_UP),
        rendemen=rendemen,
        yield_percent=yield_pct,
        complete=raw is not None,
        outputs=out_rows,
    )


def list_sortation_rendemen(
    session: Session,
    batch_id: Optional[int] = None,
    date_from: Optional[dt.date] = None,
    date_to: Optional[dt.date] = None,
) -> list[SortationRendemen]:
    """One row per SORTATION event (the SORT sheet's rows), oldest first.
    `batch_id` keeps sortations whose input OR any output is that batch."""
    stmt = select(ProcessEvent.event_id).where(ProcessEvent.event_type == EventType.SORTATION)
    if date_from is not None:
        stmt = stmt.where(ProcessEvent.event_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(ProcessEvent.event_date <= date_to)
    ids = session.execute(stmt.order_by(ProcessEvent.event_date, ProcessEvent.event_id)).scalars().all()

    attr = _Attribution(session)
    rows = [sortation_rendemen(session, eid, attr) for eid in ids]
    if batch_id is not None:
        rows = [r for r in rows
                if r.input_batch_id == batch_id or any(o.batch_id == batch_id for o in r.outputs)]
    return rows
