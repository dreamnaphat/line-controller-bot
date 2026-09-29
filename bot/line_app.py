"""Drive the LINE Android app through the selectors in config/selectors.yaml."""

import time
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional


class SearchResult(str, Enum):
    CAN_ADD = "CAN_ADD"                # found and not a friend yet
    ALREADY_FRIEND = "ALREADY_FRIEND"
    NOT_FOUND = "NOT_FOUND"            # no LINE account, or the owner turned off "Allow others to add me"
    LIMITED = "LIMITED"                # LINE temporarily blocked phone search
    UNKNOWN = "UNKNOWN"                # none of the expected results appeared


# Checked in this order on every poll, so a search-limit warning wins over anything else on screen.
_RESULT_KEYS = [
    ("result_search_limited", SearchResult.LIMITED),
    ("result_not_found", SearchResult.NOT_FOUND),
    ("result_add_button", SearchResult.CAN_ADD),
    ("result_chat_button", SearchResult.ALREADY_FRIEND),
]


class LineUIError(RuntimeError):
    pass


class LineApp:
    def __init__(self, device, selectors: Dict[str, List[Dict[str, str]]], package: str,
                 before_action: Optional[Callable[[str], None]] = None, poll_interval: float = 0.5):
        self.d = device
        self.selectors = selectors
        self.package = package
        self.before_action = before_action or (lambda step: None)
        self.poll_interval = poll_interval

    # ---- selector helpers ----

    def find(self, key: str) -> Optional[Any]:
        """First element on screen matching any selector listed under `key`, or None."""
        specs = self.selectors.get(key)
        if not specs:
            raise LineUIError(f"ยังไม่มี selector ชื่อ {key} ใน config/selectors.yaml")
        for spec in specs:
            spec = dict(spec)
            xpath = spec.pop("xpath", None)
            element = self.d.xpath(xpath) if xpath else self.d(**spec)
            if element.exists:
                return element
        return None

    def wait_for_any(self, keys: Iterable[str], timeout: float) -> Optional[str]:
        """Poll until one of `keys` is on screen and return it, or None after `timeout` seconds."""
        keys = list(keys)
        deadline = time.monotonic() + timeout
        while True:
            for key in keys:
                if self.find(key) is not None:
                    return key
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_interval)

    def _require(self, key: str, timeout: float) -> Any:
        if self.wait_for_any([key], timeout) is None:
            raise LineUIError(f"ไม่เจอ {key} บนหน้าจอ")
        return self.find(key)

    def tap(self, key: str, timeout: float = 10.0) -> None:
        self.before_action(f"กด {key}")
        self._require(key, timeout).click()

    def type_into(self, key: str, text: str, timeout: float = 10.0) -> None:
        self.before_action(f"พิมพ์ลง {key}: {text}")
        self._require(key, timeout).set_text(text)

    # ---- flows ----

    def go_home(self, max_back: int = 6) -> None:
        """Bring LINE to the front and return to the Home tab, restarting the app if backing out fails."""
        self.before_action("เปิด LINE แล้วกลับหน้าหลัก")
        self.d.app_start(self.package)
        for _ in range(max_back):
            if self.d.app_current().get("package") != self.package:
                self.d.app_start(self.package)
            home = self.find("home_tab")
            if home is not None:
                home.click()
                return
            self.d.press("back")
            time.sleep(self.poll_interval)
        self.d.app_stop(self.package)
        self.d.app_start(self.package)
        self._require("home_tab", 15.0).click()

    def search_phone(self, phone: str, timeout: float = 15.0) -> SearchResult:
        self.tap("add_friend_button")
        self.tap("search_entry")
        self.tap("phone_search_option")
        self.type_into("search_input", phone)
        self.before_action("กดค้นหา")
        submit = self.find("search_submit") if self.selectors.get("search_submit") else None
        if submit is not None:
            submit.click()
        else:
            self.d.press("enter")
        found = self.wait_for_any([key for key, _ in _RESULT_KEYS], timeout)
        return dict(_RESULT_KEYS)[found] if found else SearchResult.UNKNOWN

    def add_friend(self) -> None:
        self.tap("result_add_button")
        self._require("result_chat_button", 10.0)

    def rename_friend(self, label: str) -> None:
        self.tap("friend_name_edit")
        self.type_into("name_edit_input", label)
        self.tap("name_edit_save")

    def open_chat(self) -> None:
        self.tap("result_chat_button")
        self._require("chat_input", 10.0)

    def send_message(self, text: str, send: bool = True, timeout: float = 5.0) -> bool:
        """Type `text` into the chat box; when `send` is True, tap send and confirm the box was cleared."""
        self.type_into("chat_input", text)
        if not send:
            return False
        self.tap("chat_send_button")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            box = self.find("chat_input")
            if box is None or box.get_text() != text:
                return True
            time.sleep(self.poll_interval)
        return False
