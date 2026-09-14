import datetime as dt

import pytest

from traceability_engine import batch_number
from traceability_engine.exceptions import BatchNumberNotImplementedError


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


def test_generate_is_not_implemented():
    """BATCH_NUMBER_SPEC.md: do not implement/change the generator until the
    AA (Jenis) segment is confirmed. This must keep failing loudly."""
    with pytest.raises(BatchNumberNotImplementedError):
        batch_number.generate()
