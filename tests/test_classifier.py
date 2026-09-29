import pytest

from bot.classifier import ReplyClassifier, normalize_reply


@pytest.fixture(scope="module")
def classifier():
    return ReplyClassifier.from_config()


@pytest.mark.parametrize("text, group, reply, status, shop_state", [
    # open / general replies -> pitch
    ("เปิดค่ะ", "other", "B1", "PITCHED", "OPEN"),
    ("เปิดร้านแล้วครับ", "other", "B1", "PITCHED", "OPEN"),  # contains "ปิดร้านแล้ว"
    ("เปิดดดดด", "other", "B1", "PITCHED", "OPEN"),
    ("ใช่ครับ", "other", "B1", "PITCHED", "OPEN"),
    ("ขายอยู่ค่ะ", "other", "B1", "PITCHED", "OPEN"),
    ("มีอะไรครับ", "other", "B1", "PITCHED", "UNKNOWN"),
    ("ใครครับ", "other", "B1", "PITCHED", "UNKNOWN"),
    ("", "other", "B1", "PITCHED", "UNKNOWN"),  # sticker or photo
    ("วันนี้ปิดค่ะ", "other", "B1", "PITCHED", "UNKNOWN"),  # closed today, not closed down
    ("วันนี้ไม่เปิดค่ะ", "other", "B1", "PITCHED", "UNKNOWN"),
    # not open yet
    ("ยังไม่เปิดค่ะ", "not_open_yet", "B2", "PITCHED", "NOT_OPEN_YET"),
    ("ยังไม่ได้เปิดร้านเลยครับ", "not_open_yet", "B2", "PITCHED", "NOT_OPEN_YET"),
    ("กำลังจะเปิดเดือนหน้า", "not_open_yet", "B2", "PITCHED", "NOT_OPEN_YET"),
    ("เปิด อาทิตย์หน้า ค่ะ", "not_open_yet", "B2", "PITCHED", "NOT_OPEN_YET"),
    # closed down
    ("ปิดร้านแล้วครับ", "closed", "B3", "CLOSED", "CLOSED"),
    ("ปิดกิจการไปแล้วค่ะ", "closed", "B3", "CLOSED", "CLOSED"),
    ("เซ้งไปแล้ว", "closed", "B3", "CLOSED", "CLOSED"),
    ("ไม่เปิดแล้วค่ะ", "closed", "B3", "CLOSED", "CLOSED"),
    # refusals and complaints
    ("ไม่สนใจค่ะ", "refuse", "B5", "DECLINED", "UNKNOWN"),
    ("ไม่ สนใจ", "refuse", "B5", "DECLINED", "UNKNOWN"),
    ("เปิดค่ะ แต่มีเจ้าประจำแล้ว", "refuse", "B5", "DECLINED", "OPEN"),
    ("ใครให้เบอร์มา", "complaint", "B4", "DECLINED", "UNKNOWN"),
    ("เอาเบอร์ผมมาจากไหน", "complaint", "B4", "DECLINED", "UNKNOWN"),
    ("SPAM", "complaint", "B4", "DECLINED", "UNKNOWN"),
    ("ผิดเบอร์ครับ", "wrong_number", "B6", "DECLINED", "UNKNOWN"),
])
def test_classify(classifier, text, group, reply, status, shop_state):
    result = classifier.classify(text)
    assert (result.group, result.reply, result.status, result.shop_state) == (group, reply, status, shop_state)


def test_refusals_block_further_contact(classifier):
    for text in ["ไม่สนใจ", "ใครให้เบอร์", "ผิดเบอร์", "ปิดกิจการแล้ว"]:
        assert classifier.classify(text).do_not_contact, text
    assert not classifier.classify("เปิดค่ะ").do_not_contact


def test_only_complaints_alert_immediately(classifier):
    assert classifier.classify("อย่าทักมาอีก").alert
    assert not classifier.classify("ไม่สนใจ").alert


def test_normalize_reply():
    assert normalize_reply("  เปิด​ดดดด  ค่ะ ") == "เปิดค่ะ"
    assert normalize_reply(None) == ""
