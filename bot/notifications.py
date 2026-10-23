"""Read LINE message notifications from `adb shell dumpsys notification --noredact`.

Each LINE message posts a record like (other fields trimmed):

    NotificationRecord(0x0422adff: pkg=jp.naver.line.android ... tag=NOTIFICATION_TAG_MESSAGE ...
          when=1792754522849/1792754522849
          extras={
              android.title=String (C0 ร้านทดสอบ)
              android.text=String (ดรีมกินข้าวยัง)
              line.chat.id=String (uff50a6...)
              line.message.id=String (637404326209519804)

For stickers and photos LINE writes the sender name in front, e.g. "C0 ร้านทดสอบ ส่งสติกเกอร์".
"""

import re
from dataclasses import dataclass
from typing import List, Optional

_RECORD_START = re.compile(r"^\s*NotificationRecord\(", re.MULTILINE)
_EXTRA_KEY = re.compile(r"^\s+[\w.]+=")
_CODE = re.compile(r"^C(\d+)(?:\s|$)")


@dataclass(frozen=True)
class LineNotice:
    title: str              # chat name as shown on our side, e.g. "C12 ร้านป้าแดง"
    text: str               # message text, without the sender-name prefix LINE adds for stickers
    chat_id: Optional[str]
    message_id: Optional[str]
    when_ms: Optional[int]


def _string_extra(block: str, name: str) -> Optional[str]:
    """Value of `name=String (...)` in a record; multi-line values run until the next extras key."""
    lines = block.splitlines()
    marker = f"{name}=String ("
    for i, line in enumerate(lines):
        start = line.find(marker)
        if start < 0:
            continue
        parts = [line[start + len(marker):]]
        for following in lines[i + 1:]:
            if _EXTRA_KEY.match(following) or following.strip() == "}":
                break
            parts.append(following)
        value = "\n".join(parts).rstrip()
        return value[:-1] if value.endswith(")") else value
    return None


def parse_line_notifications(dump: str, package: str = "jp.naver.line.android") -> List[LineNotice]:
    """LINE chat message notifications in the dump, one per message (group summaries are skipped)."""
    notices, seen = [], set()
    starts = [m.start() for m in _RECORD_START.finditer(dump)] + [len(dump)]
    for begin, end in zip(starts, starts[1:]):
        block = dump[begin:end]
        header = block.splitlines()[0]
        if f"pkg={package} " not in header or "GROUP_SUMMARY" in header:
            continue
        title = _string_extra(block, "android.title")
        text = _string_extra(block, "android.text")
        if not title or text is None:
            continue
        message_id = _string_extra(block, "line.message.id")
        if message_id is not None:
            if message_id in seen:
                continue
            seen.add(message_id)
        when = re.search(r"^\s*when=(\d+)", block, re.MULTILINE)
        notices.append(LineNotice(
            title=title,
            text=strip_sender_prefix(title, text),
            chat_id=_string_extra(block, "line.chat.id"),
            message_id=message_id,
            when_ms=int(when.group(1)) if when else None,
        ))
    return notices


def strip_sender_prefix(title: str, text: str) -> str:
    """ "C0 ร้านทดสอบ ส่งสติกเกอร์" -> "ส่งสติกเกอร์" when the title is repeated in front of the text."""
    prefix = title + " "
    return text[len(prefix):] if text.startswith(prefix) else text


def outreach_id_from_title(title: str) -> Optional[int]:
    """Our friend labels start with the shop code: "C12 ร้านป้าแดง" -> 12. Other chats -> None."""
    match = _CODE.match(title.strip())
    return int(match.group(1)) if match else None
