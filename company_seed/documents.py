"""Local source registry and deterministic, evidence-addressable document text."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import fitz
from pydantic import Field, model_validator

from company_seed.schema import Category, Https, Model, Source, Text

MAX_TEXT = 40_000
MIN_TEXT = 100


class Seed(Model):
    url: Https
    source_type: Literal["CAREER_PAGE", "ABOUT_PAGE", "TECH_BLOG"]
    scope: Text = Field(max_length=100)
    verified: bool = False
    discovery: bool = False


class CompanySeed(Model):
    code: Text
    display_name: Text
    category: Category
    legal_name: Text | None = None
    boundary_note: Text
    allowed_hosts: list[Text] = Field(min_length=1)
    asset_hosts: list[Text] = Field(default_factory=list)
    dart_corp_code: str | None = Field(default=None, pattern=r"^\d{8}$")
    sources: list[Seed] = Field(min_length=1)

    @model_validator(mode="after")
    def check_source_hosts(self) -> CompanySeed:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", self.code):
            raise ValueError("기업 code 형식 오류")
        for source in self.sources:
            allowed_url(source.url, self.allowed_hosts)
        return self


class Passage(Model):
    passage_id: str
    text: Text


class Document(Model):
    source: Source
    content_hash: str
    passages: list[Passage] = Field(min_length=1)
    acquisition: Literal["http", "browser", "manual", "fixture", "dart"] = "http"


def read_registry(path: Path) -> list[CompanySeed]:
    seeds = [CompanySeed.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    if len({seed.code for seed in seeds}) != len(seeds):
        raise ValueError("중복 기업 code")
    return seeds


def allowed_url(url: str, hosts: list[str]) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password:
        raise ValueError("등록한 HTTPS 호스트 밖의 주소")
    if parsed.port not in (None, 443):
        raise ValueError("기본 HTTPS 포트만 허용")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


class PageText(HTMLParser):
    """A deliberately conservative extractor; JS-only pages remain collection failures."""

    SKIP = frozenset({"script", "style", "nav", "footer", "header", "noscript", "svg"})
    BREAK = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "section", "article", "br", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: list[str] = []
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.identity_parts: list[str] = []
        self.in_title = False
        self.published: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in self.SKIP:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag == "title":
            self.in_title = True
        if tag in self.BREAK:
            self.parts.append("\n")
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if (
            tag == "link"
            and values.get("rel") == "alternate"
            and any(kind in (values.get("type") or "") for kind in ("rss", "atom"))
            and values.get("href")
        ):
            self.links.append(values["href"] or "")
        if tag == "meta" and values.get("property") in {"article:published_time", "datePublished"}:
            self.published = values.get("content")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == "title":
            self.in_title = False
        if tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden == ["footer"]:
            self.identity_parts.append(data + "\n")
        if not self.hidden:
            (self.title_parts if self.in_title else self.parts).append(data)


def normalize_lines(text: str) -> list[str]:
    return list(dict.fromkeys(line for raw in text.splitlines() if (line := " ".join(raw.split()))))


def parse_document(source: Seed, body: bytes, content_type: str, collected: date) -> tuple[Document, list[str]]:
    url = str(source.url)
    source_key = "web-" + hashlib.sha256(url.encode()).hexdigest()[:20]
    links: list[str] = []
    published = None
    if "pdf" in content_type or body.startswith(b"%PDF-"):
        with fitz.open(stream=body, filetype="pdf") as pdf:
            title = str((pdf.metadata or {}).get("title") or urlsplit(url).path.rsplit("/", 1)[-1])
            passages = [
                Passage(passage_id=f"page-{i + 1}", text=text[:MAX_TEXT])
                for i in range(min(pdf.page_count, 50))
                if (text := pdf[i].get_text().strip())
            ]
    else:
        if "html" not in content_type and "text/plain" not in content_type:
            raise ValueError("지원하지 않는 본문 형식")
        parser = PageText()
        decoded = body.decode("utf-8", errors="replace")
        parser.feed(escape(decoded) if "text/plain" in content_type else decoded)
        title = " ".join(parser.title_parts).strip() or url
        if any(
            marker in title.casefold()
            for marker in ("access denied", "just a moment", "verify you are human", "403 forbidden")
        ):
            raise ValueError("ACCESS_CHALLENGE: 접근 확인/차단 화면을 회사 본문으로 사용하지 않음")
        lines = normalize_lines("".join(parser.parts))
        passages = [Passage(passage_id=f"p-{i + 1}", text=line) for i, line in enumerate(lines)]
        # Preserve explicit legal-name lines as evidence, without treating a footer-only JS shell as a body.
        if sum(len(p.text) for p in passages) >= MIN_TEXT:
            identity = normalize_lines("".join(parser.identity_parts))
            passages.extend(
                Passage(passage_id=f"footer-{i + 1}", text=line)
                for i, line in enumerate(identity)
                if any(marker in line for marker in ("주식회사", "(주)", "사업자", "법인"))
            )
        links = parser.links
        if parser.published:
            try:
                candidate = date.fromisoformat(parser.published[:10])
                published = candidate if candidate <= collected else None
            except ValueError:
                pass
    # Keep identity evidence, then a bounded prefix of each body passage. Quotes still match this cache.
    bounded: list[Passage] = []
    remaining = MAX_TEXT
    for passage in sorted(passages, key=lambda item: not item.passage_id.startswith("footer-")):
        if remaining <= 0:
            break
        bounded.append(Passage(passage_id=passage.passage_id, text=passage.text[:remaining]))
        remaining -= len(bounded[-1].text)
    text = "\n".join(p.text for p in bounded)
    if len(text) < MIN_TEXT:
        raise ValueError("읽을 본문 부족: JS 렌더링·이미지 PDF·차단 페이지 여부 확인 필요")
    versioned = {
        "text": text,
        "title": title,
        "published": str(published),
        "scope": source.scope,
        "sourceType": source.source_type,
        "url": url,
        "parserVersion": 2,
    }
    digest = hashlib.sha256(json.dumps(versioned, sort_keys=True).encode()).hexdigest()
    document = Document(
        source=Source(
            source_key=source_key,
            source_type=source.source_type,
            url=url,
            title=title[:200],
            scope=source.scope,
            published_at=published,
            collected_at=collected,
        ),
        content_hash=digest,
        passages=bounded,
    )
    return document, [urljoin(url, link) for link in links]
