from pathlib import Path

from bot.chat_list import ChatRow, parse_chat_list

XML = (Path(__file__).parent / "data" / "chat_list.xml").read_text(encoding="utf-8")


def test_parse_chat_list_reads_only_our_chats_with_unread_counts():
    assert parse_chat_list(XML) == [
        ChatRow("C0 ร้านทดสอบ", 0, "ดรีมก้ินข้าวยัง", 1),
        ChatRow("C12 ร้านป้าแดง", 12, "สวัสดีครับ ร้านป้าแดง เปิดขายอยู่ไหมครับ 🙏", 0),
    ]
