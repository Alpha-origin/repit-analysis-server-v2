from __future__ import annotations

import ipaddress
import re
import unicodedata

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\w)(?:\+82[-\s]?0?\d{1,2}|0\d{1,2})[-\s]?\d{3,4}[-\s]?\d{4}(?!\w)")
IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
KEY = re.compile(r"\b(?:AKIA[A-Z0-9]{16}|(?=[A-Za-z0-9_/-]*[A-Za-z])(?=[A-Za-z0-9_/-]*\d)[A-Za-z0-9_/-]{32,})\b")


def _mask_ip(match: re.Match[str]) -> str:
    try:
        ipaddress.IPv4Address(match.group())
    except ipaddress.AddressValueError:
        return match.group()
    return "[가림:IP]"


def mask_text(text: str) -> str:
    text = KEY.sub("[가림:키]", text)
    text = EMAIL.sub("[가림:이메일]", text)
    text = IP.sub(_mask_ip, text)
    return PHONE.sub("[가림:전화]", text)


def normalize_quote(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", text).lower()
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )
