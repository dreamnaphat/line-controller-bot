"""Test mode guard: while it is on, the bot may only contact the team's own phone numbers."""

from typing import Any, Dict, Optional, Set


def test_mode(settings: Dict[str, Any]) -> bool:
    return bool(settings.get("safety", {}).get("test_mode", True))


def blocked_reason(phone: str, settings: Dict[str, Any], allowed: Set[str]) -> Optional[str]:
    """Why the bot must not contact `phone`, or None when it may."""
    if not test_mode(settings) or phone in allowed:
        return None
    return (f"โหมดทดสอบเปิดอยู่ — ทักได้เฉพาะเบอร์ใน TEST_PHONES ในไฟล์ .env ({phone} ไม่อยู่ในรายการ)\n"
            "ถ้าจะทักร้านจริง ให้ตั้ง safety.test_mode: false ใน config/settings.yaml")
