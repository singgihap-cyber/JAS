"""Fase 21 -- Customer matching (resolves Fase 12 poin 7).

`services/delivery.py` #7 left `customer_id` as an optional, caller-supplied
FK with **no matching/lookup performed by the engine layer** -- resolving a
free-text recipient name (PD `PERUSAHAAN` / SmpD `NAMA`, e.g. "LIBERTA
GELATO", "MALIK S/RUSIA") to a `Customer` master-data row was explicitly
deferred as `[UNCONFIRMED]` (Fase 12 poin 7, tracked in PROJECT_STATUS.md's
open questions since Fase 15 slice 5). This module adds that matching layer.

## Decisions

1. **Suggestion-only -- this module never assigns `customer_id` itself.**
   It returns ranked candidates for a caller (the UI, `routers/master_data.py`)
   to present; the human operator still makes the final pick (or creates a
   new `Customer`), exactly as `Shipment.customer_id` already worked since
   Fase 12/15 slice 5. This preserves `delivery.py` #7's existing behavior
   (`customer_id` stays an explicit, human-driven optional field) rather
   than silently auto-linking a shipment to a possibly-wrong customer, which
   would corrupt genealogy/reporting data with no way to tell an automatic
   guess from a confirmed human choice. `Shipment.recipient` (free text)
   continues to be recorded regardless, unchanged.

2. **Normalization is formatting-only: uppercase, strip punctuation, collapse
   whitespace -- no legal-entity-suffix stripping.** Real recipient names
   sourced in `delivery.py`'s own docstring ("LIBERTA GELATO", "MCC", "SGS
   VIETNAM", "MALIK SABYTAEV") carry no `PT`/`CV`/`Tbk`-style prefixes to
   strip, so inventing suffix-stripping rules here would be guessing ahead of
   real data (CLAUDE.md rule 11). If PT JAS's `Customer` master data later
   includes such prefixes inconsistently, this should be revisited then, not
   guessed now.

3. **Algorithm: exact normalized match first, then `difflib.SequenceMatcher`
   fuzzy ratio above `SUGGESTION_THRESHOLD`.** Stdlib-only (no new dependency
   such as `rapidfuzz`/`fuzzywuzzy` added to `requirements.txt`) since a
   simple ratio is sufficient for a human-in-the-loop suggestion list, not an
   authoritative decision. `SUGGESTION_THRESHOLD = 0.5` was chosen to surface
   near-duplicates (case/whitespace/punctuation drift, minor typos) while
   excluding unrelated names; it is a UI-assist tuning constant, not a
   business rule, and can be adjusted freely without a data-model impact.

4. **Verified against the real ambiguous pair from `delivery.py` #2, not
   just assumed:** Packing's `PEMBELI` = "MALIK SABYTAEV" vs PD's
   `PERUSAHAAN` = "MALIK S/RUSIA" (abbreviated name + country appended)
   share a "MALIK S" prefix, which puts their `difflib` ratio at ~0.59 --
   just above `SUGGESTION_THRESHOLD`, so this matcher DOES surface it as a
   low-confidence (`exact=False`, score ~0.59) suggestion rather than
   staying silent, which happens to help in this specific real case
   (confirmed by `tests/test_customer_matching.py`, not guessed). This is
   incidental to the two names sharing a substring, not a general
   solution -- a genuine rename/alias pair with **no shared substring at
   all** (e.g. a customer known internally by a code entirely unrelated to
   their legal name) will still score at or near 0 and surface no
   suggestion, which is an inherent limitation of any string-similarity
   approach, not something this module can invent its way around. Solving
   that class of case would require a maintained alias/synonym table PT JAS
   does not currently have -- left `[UNCONFIRMED]` per CLAUDE.md rule 11
   rather than guessing one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")

# Tuning constant for the fuzzy-suggestion cutoff -- see module docstring #3.
SUGGESTION_THRESHOLD = 0.5


def normalize_name(name: str) -> str:
    """Formatting-only normalization -- uppercase, punctuation stripped to
    spaces, whitespace collapsed. See module docstring #2 for why nothing
    more aggressive (e.g. legal-suffix stripping) is done here."""
    if not name:
        return ""
    normalized = _PUNCT_RE.sub(" ", name.strip().upper())
    return _WHITESPACE_RE.sub(" ", normalized).strip()


@dataclass
class CustomerMatch:
    customer_id: int
    name: str
    score: float  # 1.0 = exact normalized match; otherwise a difflib ratio in [SUGGESTION_THRESHOLD, 1.0)
    exact: bool


def match_customer(session: Session, query: str, limit: int = 5) -> list[CustomerMatch]:
    """Rank existing `Customer` rows against a free-text `query` (e.g. the
    Delivery/Sample Delivery `recipient` field the operator just typed).
    Returns at most `limit` candidates, exact match(es) first, then
    descending fuzzy score. Returns `[]` for an empty/whitespace-only query
    or when no `Customer` row clears `SUGGESTION_THRESHOLD` -- an empty list
    is a normal, expected result (module docstring #1: the caller falls back
    to "no suggestion, create new or leave unattributed"), not an error.
    """
    query_norm = normalize_name(query)
    if not query_norm:
        return []

    customers = session.execute(select(Customer)).scalars().all()
    matches: list[CustomerMatch] = []
    for customer in customers:
        candidate_norm = normalize_name(customer.name)
        if not candidate_norm:
            continue
        if candidate_norm == query_norm:
            matches.append(
                CustomerMatch(customer_id=customer.customer_id, name=customer.name, score=1.0, exact=True)
            )
            continue
        ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio()
        if ratio >= SUGGESTION_THRESHOLD:
            matches.append(
                CustomerMatch(
                    customer_id=customer.customer_id,
                    name=customer.name,
                    score=round(ratio, 4),
                    exact=False,
                )
            )

    matches.sort(key=lambda m: (not m.exact, -m.score, m.name))
    return matches[:limit]
