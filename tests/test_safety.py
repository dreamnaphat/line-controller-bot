from bot import safety
from bot.safety import blocked_reason

TEAM = {"0812345678"}


def test_test_mode_is_on_unless_turned_off():
    assert safety.test_mode({})
    assert safety.test_mode({"safety": {"test_mode": True}})
    assert not safety.test_mode({"safety": {"test_mode": False}})


def test_only_team_numbers_allowed_in_test_mode():
    settings = {"safety": {"test_mode": True}}
    assert blocked_reason("0812345678", settings, TEAM) is None
    assert "TEST_PHONES" in blocked_reason("0899999999", settings, TEAM)
    assert "TEST_PHONES" in blocked_reason("0812345678", settings, set())


def test_any_number_allowed_when_test_mode_off():
    assert blocked_reason("0899999999", {"safety": {"test_mode": False}}, set()) is None


def test_test_phones_reads_env(monkeypatch):
    from bot import config

    monkeypatch.setattr(config, "_load_env", lambda: None)
    monkeypatch.setenv("TEST_PHONES", "081 234 5678, +66898765432, 02 123 4567,")
    assert config.test_phones() == {"0812345678", "0898765432"}
