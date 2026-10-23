"""A scripted stand-in for a uiautomator2 device, plus a small LINE app built from config/selectors.yaml."""

from types import SimpleNamespace

from bot.config import load_yaml

PACKAGE = "jp.naver.line.android"


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
        for field in self.device.clears.get(key(self.spec), []):
            self.device.text[field] = ""
        self.device.go(key(self.spec))

    def set_text(self, text):
        self.device.log.append(("type", self.spec, text))
        self.device.text[key(self.spec)] = text

    def get_text(self):
        return self.device.text.get(key(self.spec), "")


class FakeDevice:
    """Each screen is a list of visible selector specs; `moves` maps (screen, action) to the next screen.

    `clears` maps a clicked spec to fields whose text it empties (e.g. tapping send clears the chat box).
    """

    def __init__(self, screens, moves, start="home", foreground=PACKAGE, clears=None):
        self.screens, self.moves, self.screen = screens, moves, start
        self.foreground = foreground
        self.clears = clears or {}
        self.log, self.text = [], {}
        self.shell_output = ""                    # what `dumpsys notification` returns
        self.hierarchy = "<hierarchy></hierarchy>"  # what dump_hierarchy returns

    def shell(self, command):
        return SimpleNamespace(output=self.shell_output, exit_code=0)

    def dump_hierarchy(self):
        return self.hierarchy

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

    def sent_messages(self, chat_input_spec):
        """Texts that were in the chat box at the moment send was tapped."""
        sent, current = [], None
        for entry in self.log:
            if entry[0] == "type" and key(entry[1]) == key(chat_input_spec):
                current = entry[2]
            elif entry[0] == "click" and entry[1] is not None and current is not None and "send" in str(entry[1]):
                sent.append(current)
                current = None
        return sent


def line_world(result):
    """A fake LINE app using the real selectors. `result` is the search outcome screen:
    "found", "friend", "missing" or "limited". Returns (device, selectors)."""
    sel = {k: v[0] for k, v in load_yaml("selectors.yaml").items()}
    tab_bar = [sel["home_tab"], sel["chat_tab"]]
    screens = {
        "home": tab_bar + [sel["add_friend_button"]],
        "add": [sel["add_friend_page"], sel["search_entry"]],
        "search": [sel["phone_search_option"], sel["search_input"], sel["search_submit"]],
        "found": [sel["result_add_button"], sel["result_name"]],
        "friend": [sel["result_chat_button"], sel["result_name"]],
        "missing": [sel["result_not_found"]],
        "limited": [{"textContains": "จำกัด"}],
        "chat": [sel["chat_input"], sel["chat_send_button"], sel["chat_header_name"]],
        "profile": [sel["friend_name_edit"], sel["profile_close"]],
        "edit": [sel["name_edit_input"], sel["name_edit_save"]],
        "chats": tab_bar + [sel["chat_tab_selected"]],
    }
    moves = {
        ("home", key(sel["add_friend_button"])): "add",
        ("add", key(sel["search_entry"])): "search",
        ("search", key(sel["search_submit"])): result,
        ("found", key(sel["result_add_button"])): "friend",
        ("friend", key(sel["result_chat_button"])): "chat",
        ("chat", key(sel["chat_header_name"])): "profile",
        ("profile", key(sel["friend_name_edit"])): "edit",
        ("edit", key(sel["name_edit_save"])): "profile",
        ("profile", key(sel["profile_close"])): "chat",
        ("home", key(sel["chat_tab"])): "chats",
    }
    for screen in screens:  # back always ends up on Home
        moves.setdefault((screen, ("press", "back")), "home")
    device = FakeDevice(screens, moves, start="home",
                        clears={key(sel["chat_send_button"]): [key(sel["chat_input"])]})
    device.text[key(sel["result_name"])] = "Nong.  Nong"
    return device, load_yaml("selectors.yaml")


def show_chat_in_list(device, code):
    """Make the chat named "<code> ..." visible in the Chats tab; tapping it opens the chat."""
    spec = {"textStartsWith": f"{code} "}
    device.screens["chats"].append(spec)
    device.moves[("chats", key(spec))] = "chat"
