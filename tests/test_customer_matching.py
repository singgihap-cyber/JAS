"""Fase 21 -- unit tests for services/customer_matching.py (resolves Fase 12
poin 7). Exercises normalize_name() and match_customer() directly against
the service layer (mirrors test_receiving.py/test_qc_md.py-style unit
tests, distinct from the HTTP-level coverage in test_webapp_api.py)."""
from __future__ import annotations

import pytest

from traceability_engine.models import Customer, CustomerAlias
from traceability_engine.services.customer_matching import (
    SUGGESTION_THRESHOLD,
    add_customer_alias,
    match_customer,
    normalize_name,
)


# --- normalize_name -----------------------------------------------------


def test_normalize_name_uppercases_and_trims():
    assert normalize_name("  liberta gelato  ") == "LIBERTA GELATO"


def test_normalize_name_collapses_internal_whitespace():
    assert normalize_name("LIBERTA    GELATO") == "LIBERTA GELATO"


def test_normalize_name_strips_punctuation_to_space():
    assert normalize_name("MALIK S/RUSIA") == "MALIK S RUSIA"
    assert normalize_name("SGS, VIETNAM.") == "SGS VIETNAM"


def test_normalize_name_empty_and_none_like():
    assert normalize_name("") == ""
    assert normalize_name("   ") == ""


# --- match_customer -------------------------------------------------------


def _add_customer(session, name):
    c = Customer(name=name)
    session.add(c)
    session.flush()
    return c


def test_match_customer_empty_query_returns_empty(session):
    _add_customer(session, "LIBERTA GELATO")
    assert match_customer(session, "") == []
    assert match_customer(session, "   ") == []


def test_match_customer_no_customers_returns_empty(session):
    assert match_customer(session, "LIBERTA GELATO") == []


def test_match_customer_exact_case_insensitive_match(session):
    c = _add_customer(session, "Liberta Gelato")
    matches = match_customer(session, "LIBERTA GELATO")
    assert len(matches) == 1
    assert matches[0].customer_id == c.customer_id
    assert matches[0].exact is True
    assert matches[0].score == 1.0


def test_match_customer_exact_match_ignores_punctuation_and_whitespace(session):
    c = _add_customer(session, "MCC")
    matches = match_customer(session, "  mcc  ")
    assert len(matches) == 1
    assert matches[0].customer_id == c.customer_id
    assert matches[0].exact is True


def test_match_customer_fuzzy_suggestion_above_threshold(session):
    c = _add_customer(session, "SGS VIETNAM")
    matches = match_customer(session, "SGS VIETMAN")  # typo'd transposition
    assert len(matches) == 1
    assert matches[0].customer_id == c.customer_id
    assert matches[0].exact is False
    assert matches[0].score >= SUGGESTION_THRESHOLD


def test_match_customer_unrelated_name_below_threshold_excluded(session):
    _add_customer(session, "SGS VIETNAM")
    matches = match_customer(session, "PT ABADI JAYA SEJAHTERA")
    assert matches == []


def test_match_customer_real_ambiguous_pair_surfaces_low_confidence_suggestion(session):
    # customer_matching.py module docstring #4: this real-world pair
    # (delivery.py #2, Packing's "MALIK SABYTAEV" vs PD's "MALIK S/RUSIA")
    # shares a "MALIK S" prefix, so it clears SUGGESTION_THRESHOLD (~0.59)
    # and surfaces as a low-confidence, non-exact suggestion -- verified
    # here rather than assumed either way.
    c = _add_customer(session, "MALIK SABYTAEV")
    matches = match_customer(session, "MALIK S/RUSIA")
    assert len(matches) == 1
    assert matches[0].customer_id == c.customer_id
    assert matches[0].exact is False
    assert SUGGESTION_THRESHOLD <= matches[0].score < 0.7


def test_match_customer_no_shared_substring_at_all_finds_nothing(session):
    # The genuinely unsolved case per docstring #4: zero string overlap.
    _add_customer(session, "ZZZZZZ")
    matches = match_customer(session, "AAAAAA")
    assert matches == []


def test_match_customer_exact_ranks_above_fuzzy(session):
    exact = _add_customer(session, "LIBERTA GELATO")
    fuzzy = _add_customer(session, "LIBERTA GELATOO")  # near-duplicate, not exact
    matches = match_customer(session, "LIBERTA GELATO", limit=5)
    assert matches[0].customer_id == exact.customer_id
    assert matches[0].exact is True
    assert any(m.customer_id == fuzzy.customer_id and not m.exact for m in matches[1:])


def test_match_customer_respects_limit(session):
    for i in range(10):
        _add_customer(session, f"CUSTOMER {i}")
    matches = match_customer(session, "CUSTOMER", limit=3)
    assert len(matches) == 3


def test_match_customer_ignores_blank_name_rows(session):
    _add_customer(session, "")
    matches = match_customer(session, "ANYTHING")
    assert matches == []


# --- add_customer_alias / alias-aware matching (Fase 21 lanjutan) --------
# PT JAS confirmed 2026-09-18 that "MALIK SABYTAEV" (Packing's PEMBELI) and
# "MALIK S/RUSIA" (PD's PERUSAHAAN) are the same real customer -- this is
# no longer the "genuinely unsolved" case from module docstring #4; it is
# now solvable via a recorded CustomerAlias, tested below with that exact
# real pair.


def test_add_customer_alias_creates_row(session):
    c = _add_customer(session, "MALIK SABYTAEV")
    alias = add_customer_alias(session, c.customer_id, "MALIK S/RUSIA")
    assert alias.customer_id == c.customer_id
    assert alias.alias == "MALIK S/RUSIA"
    stored = session.query(CustomerAlias).all()
    assert len(stored) == 1


def test_add_customer_alias_unknown_customer_raises(session):
    with pytest.raises(ValueError):
        add_customer_alias(session, 999, "SOME ALIAS")


def test_add_customer_alias_empty_text_raises(session):
    c = _add_customer(session, "MALIK SABYTAEV")
    with pytest.raises(ValueError):
        add_customer_alias(session, c.customer_id, "   ")


def test_add_customer_alias_idempotent_for_same_customer(session):
    c = _add_customer(session, "MALIK SABYTAEV")
    first = add_customer_alias(session, c.customer_id, "MALIK S/RUSIA")
    second = add_customer_alias(session, c.customer_id, "malik s rusia")  # same normalized text
    assert first.alias_id == second.alias_id
    assert len(session.query(CustomerAlias).all()) == 1


def test_add_customer_alias_conflicts_with_another_customers_name(session):
    malik = _add_customer(session, "MALIK SABYTAEV")
    _add_customer(session, "MALIK S/RUSIA")  # someone already created this as its OWN customer row
    with pytest.raises(ValueError):
        add_customer_alias(session, malik.customer_id, "MALIK S/RUSIA")


def test_add_customer_alias_conflicts_with_another_customers_alias(session):
    malik = _add_customer(session, "MALIK SABYTAEV")
    other = _add_customer(session, "SGS VIETNAM")
    add_customer_alias(session, other.customer_id, "MALIK S/RUSIA")
    with pytest.raises(ValueError):
        add_customer_alias(session, malik.customer_id, "MALIK S/RUSIA")


def test_match_customer_via_alias_is_exact(session):
    c = _add_customer(session, "MALIK SABYTAEV")
    add_customer_alias(session, c.customer_id, "MALIK S/RUSIA")

    matches = match_customer(session, "MALIK S/RUSIA")
    assert len(matches) == 1
    assert matches[0].customer_id == c.customer_id
    assert matches[0].exact is True
    assert matches[0].score == 1.0
    assert matches[0].matched_alias == "MALIK S/RUSIA"


def test_match_customer_via_alias_is_case_and_punctuation_insensitive(session):
    c = _add_customer(session, "MALIK SABYTAEV")
    add_customer_alias(session, c.customer_id, "MALIK S/RUSIA")

    matches = match_customer(session, "  malik s rusia  ")
    assert len(matches) == 1
    assert matches[0].customer_id == c.customer_id
    assert matches[0].exact is True


def test_match_customer_name_match_has_no_matched_alias(session):
    c = _add_customer(session, "MALIK SABYTAEV")
    add_customer_alias(session, c.customer_id, "MALIK S/RUSIA")

    matches = match_customer(session, "MALIK SABYTAEV")
    assert len(matches) == 1
    assert matches[0].exact is True
    assert matches[0].matched_alias is None


def test_match_customer_alias_of_one_customer_does_not_leak_to_another(session):
    malik = _add_customer(session, "MALIK SABYTAEV")
    add_customer_alias(session, malik.customer_id, "MALIK S/RUSIA")
    _add_customer(session, "SGS VIETNAM")

    matches = match_customer(session, "SGS VIETNAM")
    assert len(matches) == 1
    assert matches[0].matched_alias is None
