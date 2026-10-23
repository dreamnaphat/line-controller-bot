"""Load YAML config files and secrets from .env."""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
ENV_FILE = ROOT / ".env"


def load_yaml(name: str) -> Dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Dict[str, Any]:
    return load_yaml("settings.yaml")


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)


def database_url() -> str:
    _load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or "USER:PASSWORD" in url:
        raise SystemExit("ยังไม่ได้ตั้ง DATABASE_URL ในไฟล์ .env (คัดลอกจาก .env.example แล้วใส่ connection string ของ Neon)")
    return url


def device_serial() -> Optional[str]:
    _load_env()
    return os.environ.get("DEVICE_SERIAL", "").strip() or None


def test_phones() -> Set[str]:
    """Team phone numbers from TEST_PHONES in .env (comma separated), normalized like shop phones."""
    from .phone import normalize_phone

    _load_env()
    raw = os.environ.get("TEST_PHONES", "")
    return {p for p in (normalize_phone(part).phone for part in raw.split(",")) if p}


def telegram() -> Tuple[Optional[str], Optional[str]]:
    """(bot token, chat id) from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env, or Nones."""
    _load_env()
    return (os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None,
            os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None)


def dashboard_password() -> Optional[str]:
    """DASHBOARD_PASSWORD from .env: required before the dashboard is opened to other computers."""
    _load_env()
    return os.environ.get("DASHBOARD_PASSWORD", "").strip() or None


def set_env_values(values: Dict[str, str]) -> None:
    """Write KEY=value lines into .env, keeping comments and other keys, and use the new values right away."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    for key, value in values.items():
        line = f"{key}={value}"
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for i, existing in enumerate(lines):
            if pattern.match(existing):
                lines[i] = line
                break
        else:
            lines.append(line)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.update(values)  # load_dotenv never overrides values already in the environment
