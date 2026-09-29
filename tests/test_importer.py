from bot.importer import ShopRow, plan_import, summarize


def shop(place_id, phone, name="ร้านทดสอบ", age=10):
    return ShopRow(place_id=place_id, name=name, phone_raw=phone, age_days=age, scan_id="scan-1")


def test_plan_import_marks_valid_and_invalid_rows():
    rows = plan_import([
        shop("a", "081 234 5678"),
        shop("b", "02 123 4567"),
        shop("c", None),
        shop("d", "12"),
    ], existing_phones=set())

    assert [(r.place_id, r.status, r.phone, r.invalid_reason) for r in rows] == [
        ("a", "PENDING", "0812345678", None),
        ("b", "INVALID", None, "landline"),
        ("c", "INVALID", None, "no_phone"),
        ("d", "INVALID", None, "bad_format"),
    ]
    assert rows[1].phone_raw == "02 123 4567"


def test_plan_import_skips_phones_already_contacted_or_repeated():
    rows = plan_import([
        shop("a", "+66 81 234 5678"),
        shop("b", "089 999 9999"),
        shop("c", "0899999999"),
    ], existing_phones={"0812345678"})

    assert [(r.place_id, r.status, r.invalid_reason) for r in rows] == [
        ("a", "INVALID", "duplicate"),
        ("b", "PENDING", None),
        ("c", "INVALID", "duplicate"),
    ]


def test_summarize_counts_outcomes():
    rows = plan_import([shop("a", "0812345678"), shop("b", None), shop("c", None)], existing_phones=set())
    assert summarize(rows) == {"PENDING": 1, "INVALID:no_phone": 2}
