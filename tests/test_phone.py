import pytest

from bot.phone import PhoneResult, normalize_phone


@pytest.mark.parametrize("raw, expected", [
    ("081 234 5678", "0812345678"),
    ("081-234-5678", "0812345678"),
    ("0812345678", "0812345678"),
    ("+66 81 234 5678", "0812345678"),
    ("+66812345678", "0812345678"),
    ("66812345678", "0812345678"),
    ("0066 81 234 5678", "0812345678"),
    ("(061) 234-5678", "0612345678"),
    ("๐๙๑๒๓๔๕๖๗๘", "0912345678"),
    ("081 234 5678, 089 876 5432", "0812345678"),
    ("081-234-5678 ต่อ 12", "0812345678"),
    ("081 234 5678 หรือ 02 123 4567", "0812345678"),
    (", 081 234 5678", "0812345678"),
])
def test_mobile_numbers(raw, expected):
    assert normalize_phone(raw) == PhoneResult(expected, None)


@pytest.mark.parametrize("raw, reason", [
    (None, "no_phone"),
    ("", "no_phone"),
    ("   ", "no_phone"),
    ("02 123 4567", "landline"),
    ("+66 2 123 4567", "landline"),
    ("053 123 456", "landline"),
    ("1234", "bad_format"),
    ("081 234 567", "bad_format"),
    ("0512345678", "bad_format"),
    ("โทรหาได้เลย", "bad_format"),
])
def test_unusable_numbers(raw, reason):
    assert normalize_phone(raw) == PhoneResult(None, reason)
