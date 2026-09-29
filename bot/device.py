"""Phone helpers: connection health checks and screen dumps used to find LINE selectors."""

import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import ROOT

DUMP_DIR = ROOT / "dumps"
LOG_DIR = ROOT / "logs"


@dataclass(frozen=True)
class Check:
    ok: bool
    label: str
    detail: str = ""
    required: bool = True


class PhoneConnectionError(RuntimeError):
    pass


def device_errors() -> tuple:
    """Exception types raised by adb / uiautomator2 when the phone is unreachable or misbehaves."""
    import adbutils
    from uiautomator2.exceptions import DeviceError

    return (adbutils.AdbError, DeviceError)


def connect(serial: Optional[str] = None):
    import uiautomator2 as u2

    try:
        return u2.connect(serial)
    except device_errors() as exc:
        raise PhoneConnectionError(f"ต่อมือถือไม่ได้: {exc} — รัน python -m bot doctor เพื่อหาสาเหตุ") from exc


def run_doctor(serial: Optional[str], line_package: str) -> List[Check]:
    """Check each link in the chain PC -> adb -> phone -> uiautomator2 -> LINE, stopping at the first break."""
    import adbutils

    checks = []
    # The bot talks to adb through adbutils (which ships its own adb), so the adb command is only a convenience.
    adb_path = shutil.which("adb")
    checks.append(Check(bool(adb_path), "พบคำสั่ง adb", adb_path or "ไม่บังคับ แต่ควรติดตั้ง platform-tools ไว้ใช้คำสั่ง adb devices และ scrcpy",
                        required=False))

    try:
        serials = [dev.serial for dev in adbutils.adb.device_list()]
    except Exception as exc:  # adb server not running or not installed
        return checks + [Check(False, "เชื่อมต่อ adb server", str(exc))]
    if not serials:
        return checks + [Check(False, "พบมือถือที่ต่ออยู่", "ต่อสาย USB แล้วกด \"อนุญาต\" ที่หน้าต่าง USB debugging บนมือถือ")]
    if serial is None and len(serials) > 1:
        return checks + [Check(False, "เลือกมือถือ", f"ต่ออยู่หลายเครื่อง ({', '.join(serials)}) ให้ใส่ DEVICE_SERIAL ในไฟล์ .env")]
    checks.append(Check(True, "พบมือถือที่ต่ออยู่", ", ".join(serials)))

    errors = device_errors()
    try:
        d = connect(serial)
        info = d.device_info
        checks.append(Check(True, "อ่านข้อมูลมือถือ",
                            f"{info.get('brand')} {info.get('model')} Android {info.get('version')} (SDK {info.get('sdk')})"))
    except (PhoneConnectionError, *errors) as exc:
        return checks + [Check(False, "อ่านข้อมูลมือถือ", str(exc))]

    try:
        version = d.app_info(line_package).get("versionName")
        checks.append(Check(True, "ติดตั้ง LINE แล้ว", f"เวอร์ชัน {version} (จดไว้ใน config/selectors.yaml)"))
    except Exception:
        checks.append(Check(False, "ติดตั้ง LINE แล้ว", f"ไม่พบแอป {line_package} บนมือถือ"))

    try:
        screen_on = bool(d.info.get("screenOn"))  # first call installs and starts the uiautomator helper
    except errors as exc:
        return checks + [Check(False, "ตัวควบคุมหน้าจอ (uiautomator) พร้อม",
                               f"{exc} — ถ้ามีหน้าต่างให้ยืนยันการติดตั้งบนมือถือ ให้กดอนุญาตแล้วรันใหม่")]
    checks.append(Check(True, "ตัวควบคุมหน้าจอ (uiautomator) พร้อม"))
    checks.append(Check(screen_on, "หน้าจอเปิดอยู่", "" if screen_on else "เปิดหน้าจอ และตั้งการล็อกหน้าจอเป็น \"ไม่มี\""))

    try:
        output = d.shell("dumpsys notification --noredact").output
    except errors as exc:
        return checks + [Check(False, "อ่านการแจ้งเตือนได้", str(exc))]
    checks.append(Check("NotificationRecord" in output, "อ่านการแจ้งเตือนได้",
                        f"ตอนนี้มีการแจ้งเตือนของ LINE {output.count(f'pkg={line_package}')} รายการ"))
    return checks


def summarize_hierarchy(xml: str) -> str:
    """One line per element that has an id, text or description, or can be tapped."""
    lines = []
    for node in ET.fromstring(xml).iter("node"):
        rid, text, desc = node.get("resource-id", ""), node.get("text", ""), node.get("content-desc", "")
        clickable = node.get("clickable") == "true"
        if not (rid or text or desc or clickable):
            continue
        lines.append(" | ".join([
            rid or "-",
            f"text={text!r}",
            f"desc={desc!r}",
            node.get("class", ""),
            "clickable" if clickable else "-",
            node.get("bounds", ""),
        ]))
    return "\n".join(lines) + "\n"


def dump_screen(d, label: str, out_dir: Path = DUMP_DIR) -> Path:
    """Save <time>-<label>.png, .xml (full hierarchy) and .txt (summary) and return the common path prefix."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # \w alone drops Thai tone marks and vowels (Unicode category Mn), so keep the Thai block explicitly
    safe_label = re.sub(r"[^\w฀-๿\-]+", "_", label).strip("_") or "screen"
    base = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{safe_label}"
    d.screenshot(f"{base}.png")
    xml = d.dump_hierarchy()
    Path(f"{base}.xml").write_text(xml, encoding="utf-8")
    Path(f"{base}.txt").write_text(summarize_hierarchy(xml), encoding="utf-8")
    return base
