import pytest

from bot import status


@pytest.fixture(autouse=True)
def bot_files(tmp_path, monkeypatch):
    """Keep every test away from the real logs/ folder (bot status, stop request, logs)."""
    files = tmp_path / "bot-files"
    files.mkdir()
    for name, filename in [("STATUS_FILE", "bot-status.json"), ("STOP_FILE", "bot-stop"),
                           ("WORKER_LOG", "worker.log"), ("CONSOLE_LOG", "bot-console.log")]:
        monkeypatch.setattr(status, name, files / filename)
    return files
