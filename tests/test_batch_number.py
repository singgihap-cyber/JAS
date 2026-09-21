import datetime as dt

import pytest

from traceability_engine import batch_number


def test_parse_historical_two_digit_supplier():
    # Proses.docx worked example / PB sheet row 1 (BATCH_NUMBER_SPEC.md anchor)
    c = batch_number.parse("030224-260221-00")
    assert c.jenis_code == "03"
    assert c.grade_code == "02"
    assert c.supplier_code == "24"
    assert c.supplier_code_width == 2
    assert c.receiving_date == dt.date(2026, 2, 21)
    assert c.process_code == "00"


def test_parse_new_three_digit_supplier():
    c = batch_number.parse("0302024-260221-00")
    assert c.jenis_code == "03"
    assert c.grade_code == "02"
    assert c.supplier_code == "024"
    assert c.supplier_code_width == 3
    assert c.receiving_date == dt.date(2026, 2, 21)


def test_parse_mixing_output_example():
    # GENEALOGY.md §7.1 worked example
    c = batch_number.parse("010100-260120-03")
    assert c.supplier_code == "00"  # zeroed once several suppliers' material blended
    assert c.process_code == "03"  # Mixing


def test_parse_rejects_unrecognized_format():
    with pytest.raises(ValueError):
        batch_number.parse("not-a-batch-number")


def _gen(**kw):
    base = dict(jenis_code="02", grade_code="01", supplier_code="18",
                receiving_date=dt.date(2026, 9, 21))
    base.update(kw)
    return batch_number.generate(**base)


def test_generate_pads_supplier_to_three_digits():
    assert _gen() == "0201018-260921-00"
    assert _gen(supplier_code="024") == "0201024-260921-00"


def test_generate_roundtrips_through_parse():
    c = batch_number.parse(_gen(jenis_code="01", grade_code="00"))
    assert (c.jenis_code, c.grade_code, c.supplier_code, c.process_code) == ("01", "00", "018", "00")
    assert c.supplier_code_width == 3 and c.receiving_date == dt.date(2026, 9, 21)
    assert not c.jenis_is_legacy


@pytest.mark.parametrize("kw", [
    {"jenis_code": "03"}, {"jenis_code": "04"}, {"jenis_code": "1"},
    {"grade_code": "07"}, {"grade_code": "10"},
    {"supplier_code": "1234"}, {"supplier_code": "ab"},
    {"process_code": "03"}, {"process_code": "01"},
])
def test_generate_rejects_invalid(kw):
    with pytest.raises(ValueError):
        _gen(**kw)


def test_generate_accepts_grade_04_and_06():
    assert _gen(grade_code="04").startswith("0204")
    assert _gen(grade_code="06").startswith("0206")



def test_jenis_official_codes_are_two():
    """Fase 30: hanya dua jenis resmi, 01 Tahitensis dan 02 Planifolia."""
    assert batch_number.JENIS_CODES == {"01": "Tahitensis", "02": "Planifolia"}
    c = batch_number.parse("0102018-260921-00")
    assert (c.jenis_label, c.jenis_is_legacy) == ("Tahitensis", False)
    c = batch_number.parse("0200018-260921-00")  # Hijau = grade 00, jenis Planifolia
    assert (c.jenis_label, c.jenis_is_legacy, c.grade_code) == ("Planifolia", False, "00")


def test_jenis_legacy_codes_still_parse_but_flagged():
    """Riwayat 03 (Planifolia lama) dan 04 (intake Hijau lama) tetap terbaca."""
    c = batch_number.parse("030224-260221-00")
    assert c.jenis_is_legacy and c.jenis_label.startswith("Planifolia")
    c = batch_number.parse("040018-260505-00")
    assert c.jenis_is_legacy and c.jenis_label.startswith("Hijau")
    assert c.grade_code == "00"


def test_jenis_unknown_code_parses_without_label():
    c = batch_number.parse("090224-260221-00")
    assert c.jenis_label is None and not c.jenis_is_legacy


def test_hijau_is_grade_not_jenis():
    assert "00" not in batch_number.JENIS_CODES
    assert batch_number.BB_TO_GRADE_MASTER["00"] == 0
