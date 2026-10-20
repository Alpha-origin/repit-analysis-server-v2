"""Optional exact, unique legal-entity matching against OpenDART, never fuzzy assignment."""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import httpx
from pydantic import Field

from company_seed.discovery import parse_xml
from company_seed.documents import CompanySeed
from company_seed.schema import Model, Package, Source, Text
from company_seed.storage import write_json

BASE = "https://opendart.fss.or.kr/api/"
MAX_DIRECTORY_BYTES = 80_000_000


class DartIdentity(Model):
    corp_code: str = Field(pattern=r"^\d{8}$")
    legal_name: Text
    business_reg_no: str | None = Field(default=None, pattern=r"^\d{10}$")
    checked_at: date


def normalized(name: str) -> str:
    return re.sub(r"\s+", "", name.replace("주식회사", "").replace("(주)", "").replace("㈜", "")).casefold()


def exact_matches(rows: list[dict[str, str]], company: CompanySeed) -> list[dict[str, str]]:
    if company.dart_corp_code:
        return [row for row in rows if row["corp_code"] == company.dart_corp_code]
    name = normalized(company.legal_name or company.display_name)
    return [row for row in rows if normalized(row["corp_name"]) == name]


class DartClient:
    def __init__(self, client: httpx.AsyncClient, key: str, cache: Path) -> None:
        self.client = client
        self.key = key
        self.cache = cache
        self.directory: list[dict[str, str]] | None = None

    async def _get(self, endpoint: str, params: dict[str, str], *, cap: int) -> bytes:
        # Response diagnostics must never expose URLs containing crtfc_key.
        try:
            async with self.client.stream("GET", BASE + endpoint, params={"crtfc_key": self.key, **params}) as response:
                if response.status_code != 200:  # noqa: PLR2004
                    raise ValueError(f"DART HTTP {response.status_code}")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > cap:
                        raise ValueError("DART 응답 크기 상한 초과")
                return bytes(data)
        except httpx.HTTPError:
            raise ValueError("DART 연결 실패; 키 값과 요청 URL은 기록하지 않음") from None

    async def load_directory(self, today: date) -> list[dict[str, str]]:
        if self.directory is not None:
            return self.directory
        path = self.cache / "directory.json"
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if today - timedelta(days=7) <= date.fromisoformat(previous["checkedAt"]) <= today:
                self.directory = previous["companies"]
                return self.directory or []
        body = await self._get("corpCode.xml", {}, cap=10_000_000)
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                info = next(item for item in archive.infolist() if item.filename.lower().endswith(".xml"))
                if info.file_size > MAX_DIRECTORY_BYTES:
                    raise ValueError("DART 디렉터리 압축 해제 상한 초과")
                root = parse_xml(archive.read(info))
        except (zipfile.BadZipFile, StopIteration):
            raise ValueError("DART 고유번호 응답이 ZIP/XML이 아님; 키·API 상태 확인") from None
        self.directory = [
            {"corp_code": node.findtext("corp_code", ""), "corp_name": node.findtext("corp_name", "")}
            for node in root.findall("list")
        ]
        write_json(path, {"checkedAt": today.isoformat(), "companies": self.directory})
        return self.directory

    async def resolve(self, company: CompanySeed, today: date) -> tuple[DartIdentity | None, str]:
        matches = exact_matches(await self.load_directory(today), company)
        if len(matches) != 1:
            return None, "ambiguous" if matches else "unmatched"
        code = matches[0]["corp_code"]
        if not re.fullmatch(r"\d{8}", code):
            raise ValueError("DART 디렉터리의 고유번호 형식 오류")
        path = self.cache / f"{code}.json"
        if path.exists():
            identity = DartIdentity.model_validate_json(path.read_text(encoding="utf-8"))
            if (
                identity.corp_code == code
                and normalized(identity.legal_name) == normalized(company.legal_name or company.display_name)
                and today - timedelta(days=7) <= identity.checked_at <= today
            ):
                return identity, "cached"
        body = await self._get("company.json", {"corp_code": code}, cap=1_000_000)
        result = json.loads(body)
        if result.get("status") != "000":
            raise ValueError(f"DART 기업개황 상태 {result.get('status', 'unknown')}")
        if result.get("corp_code") != code:
            raise ValueError("DART 응답 고유번호 불일치")
        expected = normalized(company.legal_name or company.display_name)
        if normalized(result["corp_name"]) != expected:
            return None, "name_mismatch"
        number = result.get("bizr_no") or None
        identity = DartIdentity(
            corp_code=code, legal_name=result["corp_name"], business_reg_no=number, checked_at=today
        )
        write_json(path, identity.model_dump(by_alias=True, mode="json"))
        return identity, "matched"


def enrich(package: Package, identity: DartIdentity | None) -> Package:
    if identity is None:
        return package
    if normalized(package.legal_name) != normalized(identity.legal_name):
        raise ValueError("추출 법인명과 DART 법인명이 달라 식별자를 연결할 수 없음")
    source = Source(
        source_key="dart-identity",
        source_type="DART",
        url=BASE + "company.json?corp_code=" + identity.corp_code,
        title="OpenDART 기업개황: " + identity.legal_name,
        scope="법인 식별 정보; 서비스·인재상 근거 아님",
        collected_at=identity.checked_at,
    )
    data = package.model_dump()
    data.update(
        dart_corp_code=identity.corp_code,
        business_reg_no=identity.business_reg_no,
        sources=[*package.sources, source],
        data_as_of=max(package.data_as_of, identity.checked_at),
    )
    return Package.model_validate(data)
