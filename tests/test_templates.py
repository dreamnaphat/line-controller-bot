import pytest

from bot.templates import TemplateError, friend_label, load_messages, render, shop_display_name


def test_render_fills_placeholders():
    assert render("สวัสดีครับ {shop} เปิดขายอยู่ไหมครับ", shop="ร้านป้าแดง") == "สวัสดีครับ ร้านป้าแดง เปิดขายอยู่ไหมครับ"


@pytest.mark.parametrize("values", [{}, {"factory": ""}, {"factory": "   "}, {"factory": None}])
def test_render_refuses_missing_or_blank_values(values):
    with pytest.raises(TemplateError, match="factory"):
        render("โรงงานลูกชิ้น{factory}", **values)


def test_configured_opener_renders_with_only_the_shop_name():
    messages = load_messages()
    assert render(messages["Q2"], shop="ร้านป้าแดง") == "สวัสดีครับ ร้านป้าแดง เปิดขายอยู่ไหมครับ 🙏"


def test_all_configured_messages_use_known_placeholders():
    known = {"shop": "ร้าน", "factory": "โรงงาน", "persona": "ต้น", "source": "Google Maps"}
    for key, template in load_messages().items():
        assert render(template, **known), key


@pytest.mark.parametrize("name, expected", [
    ("ก๋วยเตี๋ยวเรือป้าแดง", "ร้านก๋วยเตี๋ยวเรือป้าแดง"),
    ("ร้านก๋วยเตี๋ยวเรือป้าแดง", "ร้านก๋วยเตี๋ยวเรือป้าแดง"),
    ("ก๋วยเตี๋ยวเรือป้าแดง - Boat Noodle", "ร้านก๋วยเตี๋ยวเรือป้าแดง"),
    ("  ก๋วยเตี๋ยว   ต้มยำ | สาขา 2 ", "ร้านก๋วยเตี๋ยว ต้มยำ"),
    ("Noodle House", "ร้าน Noodle House"),
    ("", "ทางร้าน"),
    (None, "ทางร้าน"),
])
def test_shop_display_name(name, expected):
    assert shop_display_name(name) == expected


def test_shop_display_name_is_truncated():
    assert len(shop_display_name("ก" * 100)) == len("ร้าน") + 40


def test_friend_label_fits_line_limit():
    assert friend_label("C1023", "ก๋วยเตี๋ยวเรือป้าแดง สาขาบางนา") == "C1023 ก๋วยเตี๋ยวเรือ"
    assert len(friend_label("C1023", "ก๋วยเตี๋ยวเรือป้าแดง สาขาบางนา")) <= 20
    assert friend_label("C7", None) == "C7"


def test_friend_label_does_not_end_with_a_leading_vowel():
    # "C1 " + 16 chars cut right after "เ" would leave a dangling vowel
    assert not friend_label("C1", "กกกกกกกกกกกกกกกกเกก").endswith("เ")
