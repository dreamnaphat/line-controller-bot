import pytest

from bot.line_app import LineApp, LineUIError, SearchResult

PACKAGE = "jp.naver.line.android"

SELECTORS = {
    "home_tab": [{"text": "home"}],
    "add_friend_button": [{"description": "add-friends"}],
    "search_entry": [{"text": "search"}],
    "phone_search_option": [{"text": "phone"}],
    "search_input": [{"className": "EditText"}],
    "result_add_button": [{"text": "add"}],
    "result_chat_button": [{"resourceId": "chat-button"}],
    "result_not_found": [{"textContains": "not found"}],
    "result_search_limited": [{"xpath": "//limit"}],
    "chat_input": [{"resourceId": "chat-input"}],
    "chat_send_button": [{"description": "send"}],
}


def key(spec):
    return tuple(sorted(spec.items()))


class FakeElement:
    def __init__(self, device, spec):
        self.device, self.spec = device, spec

    @property
    def exists(self):
        return key(self.spec) in {key(s) for s in self.device.screens[self.device.screen]}

    def click(self):
        self.device.log.append(("click", self.spec))
        self.device.go(key(self.spec))

    def set_text(self, text):
        self.device.log.append(("type", self.spec, text))
        self.device.text[key(self.spec)] = text

    def get_text(self):
        return self.device.text.get(key(self.spec), "")


class FakeDevice:
    """Each screen is a list of visible selector specs; `moves` maps (screen, action) to the next screen."""

    def __init__(self, screens, moves, start="home", foreground=PACKAGE):
        self.screens, self.moves, self.screen = screens, moves, start
        self.foreground = foreground
        self.log, self.text = [], {}

    def go(self, action):
        self.screen = self.moves.get((self.screen, action), self.screen)

    def __call__(self, **spec):
        return FakeElement(self, spec)

    def xpath(self, xpath):
        return FakeElement(self, {"xpath": xpath})

    def press(self, name):
        self.log.append(("press", name))
        self.go(("press", name))

    def app_start(self, package, **kwargs):
        self.log.append(("start", package))
        self.foreground = package

    def app_stop(self, package):
        self.log.append(("stop", package))
        self.screen = "home"

    def app_current(self):
        return {"package": self.foreground}


def search_device(result_screen):
    screens = {
        "home": [{"text": "home"}, {"description": "add-friends"}],
        "add": [{"text": "search"}],
        "search": [{"text": "phone"}, {"className": "EditText"}],
        "found": [{"text": "add"}],
        "friend": [{"resourceId": "chat-button"}],
        "missing": [{"textContains": "not found"}],
        "limited": [{"xpath": "//limit"}, {"text": "add"}],
        "blank": [],
        "chat": [{"resourceId": "chat-input"}, {"description": "send"}],
    }
    moves = {
        ("home", key({"description": "add-friends"})): "add",
        ("add", key({"text": "search"})): "search",
        ("search", ("press", "enter")): result_screen,
        ("found", key({"text": "add"})): "friend",
        ("friend", key({"resourceId": "chat-button"})): "chat",
    }
    return FakeDevice(screens, moves)


def app_for(device, selectors=SELECTORS):
    return LineApp(device, selectors, PACKAGE, poll_interval=0.001)


@pytest.mark.parametrize("screen, expected", [
    ("found", SearchResult.CAN_ADD),
    ("friend", SearchResult.ALREADY_FRIEND),
    ("missing", SearchResult.NOT_FOUND),
    ("limited", SearchResult.LIMITED),  # the limit warning wins even though an add button is visible
])
def test_search_phone_detects_result(screen, expected):
    device = search_device(screen)
    assert app_for(device).search_phone("0812345678") == expected
    assert ("type", {"className": "EditText"}, "0812345678") in device.log


def test_search_phone_reports_unknown_when_nothing_expected_appears():
    assert app_for(search_device("blank")).search_phone("0812345678", timeout=0.01) == SearchResult.UNKNOWN


def test_add_then_type_message_without_sending():
    device = search_device("found")
    app = app_for(device)
    app.search_phone("0812345678")
    app.add_friend()
    app.open_chat()
    assert app.send_message("สวัสดีครับ", send=False) is False
    assert device.text[key({"resourceId": "chat-input"})] == "สวัสดีครับ"
    assert ("click", {"description": "send"}) not in device.log


def test_send_message_taps_send_and_confirms_box_cleared():
    device = search_device("friend")
    app = app_for(device)
    app.search_phone("0812345678")
    app.open_chat()

    original_click = FakeElement.click

    def click_and_clear(element):
        original_click(element)
        if element.spec == {"description": "send"}:
            device.text[key({"resourceId": "chat-input"})] = ""

    FakeElement.click = click_and_clear
    try:
        assert app.send_message("สวัสดีครับ", send=True, timeout=0.05) is True
    finally:
        FakeElement.click = original_click


def test_send_message_reports_failure_when_text_stays_in_box():
    device = search_device("friend")
    app = app_for(device)
    app.search_phone("0812345678")
    app.open_chat()
    assert app.send_message("สวัสดีครับ", send=True, timeout=0.01) is False


def test_go_home_backs_out_until_home_tab_is_visible():
    screens = {"deep": [], "middle": [], "home": [{"text": "home"}]}
    moves = {("deep", ("press", "back")): "middle", ("middle", ("press", "back")): "home"}
    device = FakeDevice(screens, moves, start="deep")
    app_for(device).go_home()
    assert device.log.count(("press", "back")) == 2
    assert device.log[-1] == ("click", {"text": "home"})


def test_go_home_restarts_line_when_backing_out_fails():
    device = FakeDevice({"stuck": [], "home": [{"text": "home"}]}, {}, start="stuck")
    app_for(device).go_home(max_back=2)
    assert ("stop", PACKAGE) in device.log


def test_missing_selector_names_the_key():
    selectors = dict(SELECTORS)
    del selectors["search_entry"]
    with pytest.raises(LineUIError, match="search_entry"):
        app_for(search_device("found"), selectors).search_phone("0812345678")


def test_tap_fails_when_element_never_appears():
    app = LineApp(FakeDevice({"home": []}, {}), SELECTORS, PACKAGE, poll_interval=0.001)
    with pytest.raises(LineUIError, match="add_friend_button"):
        app.tap("add_friend_button", timeout=0.01)
