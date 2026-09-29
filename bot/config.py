"""Load YAML config files and secrets from .env."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_yaml(name: str) -> Dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Dict[str, Any]:
    return load_yaml("settings.yaml")


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")


def database_url() -> str:
    _load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or "USER:PASSWORD" in url:
        raise SystemExit("ยังไม่ได้ตั้ง DATABASE_URL ในไฟล์ .env (คัดลอกจาก .env.example แล้วใส่ connection string ของ Neon)")
    return url


def device_serial() -> Optional[str]:
    _load_env()
    return os.environ.get("DEVICE_SERIAL", "").strip() or None
