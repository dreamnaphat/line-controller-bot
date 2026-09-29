"""Normalize Thai phone numbers (as stored from Google Maps) into the format LINE phone search uses."""

import re
from dataclasses import dataclass
from typing import Optional

MOBILE_PREFIXES = ("06", "08", "09")
LANDLINE_PREFIXES = ("02", "03", "04", "05", "07")  # 9 digits including the area code
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
# When several numbers or an extension are listed, only the first number is used.
_SEPARATORS = re.compile(r"[,/;#]|ต่อ|หรือ|ext", re.IGNORECASE)


@dataclass(frozen=True)
class PhoneResult:
    phone: Optional[str]    # 10-digit mobile number such as "0812345678", or None when unusable
    reason: Optional[str]   # why unusable: "no_phone" | "landline" | "bad_format"


def normalize_phone(raw: Optional[str]) -> PhoneResult:
    if raw is None or not raw.strip():
        return PhoneResult(None, "no_phone")

    parts = [p for p in _SEPARATORS.split(raw.translate(_THAI_DIGITS)) if re.search(r"[0-9]", p)]
    digits = re.sub(r"[^0-9]", "", parts[0]) if parts else ""
    if digits.startswith("0066"):
        digits = digits[2:]
    if digits.startswith("66"):
        digits = "0" + digits[2:]

    if len(digits) == 10 and digits.startswith(MOBILE_PREFIXES):
        return PhoneResult(digits, None)
    if len(digits) == 9 and digits.startswith(LANDLINE_PREFIXES):
        return PhoneResult(None, "landline")
    return PhoneResult(None, "bad_format")
