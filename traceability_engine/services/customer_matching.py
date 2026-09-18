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
   approach, not something this module can invent its way around.

5. **`CustomerAlias` (Fase 21 lanjutan) -- PT JAS has since confirmed
   "MALIK SABYTAEV" and "MALIK S/RUSIA" from #4 above ARE the same real
   customer, canonical name "MALIK SABYTAEV" (user-confirmed 2026-09-18,
   not guessed).** Rather than hardcode that one fact, this module now
   supports a general, DB-backed alias table (`models.CustomerAlias`):
   `add_customer_alias()` records a confirmed synonym for a `Customer`, and
   `match_customer()` treats a query matching a recorded alias with
   `exact=True` -- the same certainty as matching `Customer.name` itself,
   because an alias is a firm human confirmation, not a string-similarity
   guess. This is different in kind from #3's fuzzy layer: fuzzy scoring
   only ever produces a *suggestion* (never `exact=True`) because it can be
   wrong; an alias is only ever created by a human confirming an identity,
   so it carries that same confidence once recorded. `add_customer_alias()`
   refuses (raises `ValueError`) to record an alias whose normalized text
   already resolves to a *different* `Customer` (via their name or another
   of their aliases) -- a contradictory mapping is rejected rather than
   silently overwritten, and re-adding an alias that already exists for the
   *same* customer is a no-op (returns the existing row) rather than an
   error, so callers don't need to check existence first. Aliases are
   matched by normalized-exact-equality only, not fuzzed further -- the
   entire point of an alias is a known, deterministic variant spelling; a
   *typo* of a known alias is still caught by `normalize_name()` (case/
   punctuation/whitespace) the same way a typo'd `Customer.name` is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, CustomerAlias

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
    score: float  # 1.0 = exact normalized match (by name OR by a confirmed alias); otherwise a difflib ratio in [SUGGESTION_THRESHOLD, 1.0)
    exact: bool
    matched_alias: Optional[str] = None  # the raw alias text that matched, if this hit came via CustomerAlias (module docstring #5)


def add_customer_alias(session: Session, customer_id: int, alias: str) -> CustomerAlias:
    """Record a confirmed alias/synonym for an existing `Customer` -- see
    module docstring #5. Raises `ValueError` if `customer_id` doesn't exist,
    the alias text is empty, or the normalized alias text already resolves
    to a *different* customer (via their name or one of their own aliases).
    Idempotent for the same customer: re-adding an alias already recorded
    for this exact customer returns the existing row rather than creating a
    duplicate or raising.
    """
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"Customer id={customer_id} does not exist.")

    alias_text = alias.strip()
    if not alias_text:
        raise ValueError("Alias text must not be empty.")
    alias_norm = normalize_name(alias_text)
    if not alias_norm:
        raise ValueError("Alias text must not be empty.")

    for other in session.execute(select(Customer)).scalars().all():
        if other.customer_id != customer_id and normalize_name(other.name) == alias_norm:
            raise ValueError(
                f"'{alias_text}' already names a different Customer "
                f"({other.name!r}, id={other.customer_id})."
            )

    for existing in session.execute(select(CustomerAlias)).scalars().all():
        if normalize_name(existing.alias) == alias_norm:
            if existing.customer_id == customer_id:
                return existing
            raise ValueError(
                f"'{alias_text}' is already an alias of a different Customer "
                f"(id={existing.customer_id})."
            )

    new_alias = CustomerAlias(customer_id=customer_id, alias=alias_text)
    session.add(new_alias)
    session.flush()
    return new_alias


def match_customer(session: Session, query: str, limit: int = 5) -> list[CustomerMatch]:
    """Rank existing `Customer` rows against a free-text `query` (e.g. the
    Delivery/Sample Delivery `recipient` field the operator just typed).
    Returns at most `limit` candidates, exact match(es) first (by name or by
    a confirmed `CustomerAlias`, module docstring #5), then descending fuzzy
    score. Returns `[]` for an empty/whitespace-only query or when no
    `Customer` row clears `SUGGESTION_THRESHOLD` -- an empty list is a
    normal, expected result (module docstring #1: the caller falls back to
    "no suggestion, create new or leave unattributed"), not an error.
    """
    query_norm = normalize_name(query)
    if not query_norm:
        return []

    customers = session.execute(select(Customer)).scalars().all()
    aliases = session.execute(select(CustomerAlias)).scalars().all()

    # customer_id -> raw alias text, for customers whose alias matches the
    # query exactly (normalized). Checked before fuzzy scoring so a matched
    # alias always wins over a coincidental fuzzy score for the same
    # customer -- module docstring #5.
    alias_hit_by_customer: dict[int, str] = {}
    for alias_row in aliases:
        if normalize_name(alias_row.alias) == query_norm:
            alias_hit_by_customer.setdefault(alias_row.customer_id, alias_row.alias)

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
        if customer.customer_id in alias_hit_by_customer:
            matches.append(
                CustomerMatch(
                    customer_id=customer.customer_id,
                    name=customer.name,
                    score=1.0,
                    exact=True,
                    matched_alias=alias_hit_by_customer[customer.customer_id],
                )
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
