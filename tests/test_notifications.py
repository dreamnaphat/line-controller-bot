from pathlib import Path

from bot.notifications import LineNotice, outreach_id_from_title, parse_line_notifications, strip_sender_prefix

DUMP = (Path(__file__).parent / "data" / "line_notifications.txt").read_text(encoding="utf-8")


def test_parse_skips_summaries_duplicates_and_other_apps():
    notices = parse_line_notifications(DUMP)
    assert notices == [
        LineNotice("C0 ร้านทดสอบ", "ดรีมกินข้าวยัง", "u0000000000000000000000000000aaaa", "637404326209519999",
                   1792754522849),
        LineNotice("C0 ร้านทดสอบ", "ส่งสติกเกอร์", "u0000000000000000000000000000aaaa", "637404326209519804",
                   1792753888331),
        LineNotice("Apisit Love U", "ข้อความส่วนตัว", "u0000000000000000000000000000bbbb", "637404326209510000",
                   1792753900000),
        LineNotice("C12 ร้านป้าแดง", "เปิดค่ะ\nมีอะไรคะ", "u0000000000000000000000000000cccc", "637404326209520001",
                   1792753999999),
    ]


def test_parse_handles_an_empty_dump():
    assert parse_line_notifications("") == []


def test_strip_sender_prefix():
    assert strip_sender_prefix("C0 ร้านทดสอบ", "C0 ร้านทดสอบ ส่งรูปภาพ") == "ส่งรูปภาพ"
    assert strip_sender_prefix("C0 ร้านทดสอบ", "เปิดค่ะ") == "เปิดค่ะ"


def test_outreach_id_from_title():
    assert outreach_id_from_title("C12 ร้านป้าแดง") == 12
    assert outreach_id_from_title("C7") == 7
    assert outreach_id_from_title("Apisit Love U") is None
    assert outreach_id_from_title("C12ร้าน") is None
    assert outreach_id_from_title("CC1 x") is None
