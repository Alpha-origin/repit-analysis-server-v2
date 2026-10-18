"""RSS/Atom/sitemap locations are discovery only, never a substitute for article evidence."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urljoin

MAX_LINKS = 100


def parse_xml(body: bytes) -> ET.Element:
    lowered = body.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("XML entity/doctype 선언은 허용하지 않습니다")
    try:
        return ET.fromstring(body)  # noqa: S314 -- entities rejected above; parser does not resolve external resources
    except ET.ParseError as exc:
        raise ValueError("XML 파싱 실패") from exc


def locations(body: bytes, url: str) -> tuple[list[str], bool]:
    root = parse_xml(body)
    kind = root.tag.rsplit("}", 1)[-1].lower()
    if kind not in {"rss", "feed", "urlset", "sitemapindex"}:
        raise ValueError("RSS/Atom/사이트맵 문서가 아닙니다")
    result: list[str] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag == "loc" and node.text:
            result.append(urljoin(url, node.text.strip()))
        elif tag == "link":
            if node.attrib.get("rel", "alternate") != "alternate":
                continue
            target = node.attrib.get("href") or node.text
            if target and target.strip():
                result.append(urljoin(url, target.strip()))
        if len(result) >= MAX_LINKS:
            break
    return list(dict.fromkeys(result)), kind == "sitemapindex"
