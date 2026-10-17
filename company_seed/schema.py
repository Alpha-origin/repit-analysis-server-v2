from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Https = Annotated[str, StringConstraints(pattern=r"^https://[^\s/]+(?:/[^\s]*)?$")]
Kind = Literal["BUSINESS", "PROBLEM", "TALENT", "MISSION_VISION", "TECH_STACK"]
Category = Literal["IT_SERVICE", "PLATFORM", "FINTECH", "GAME", "CONSUMER", "SECURITY"]
FORBIDDEN = ("공식", "기출", "저희", "우리 회사", "합격")


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Source(Model):
    source_key: Text = Field(max_length=40)
    source_type: Literal[
        "CAREER_PAGE",
        "ABOUT_PAGE",
        "TECH_BLOG",
        "DART",
        "FTC",
        "MID_SIZE_REGISTRY",
        "SME_REGISTRY",
        "GITHUB",
        "JOB_API",
        "SAMPLE",
    ]
    url: Https
    title: str | None = Field(default=None, max_length=200)
    scope: Text = Field(max_length=100)
    published_at: date | None = None
    collected_at: date


class Fact(Model):
    kind: Kind
    title: Text = Field(max_length=20)
    content: Text = Field(max_length=200)
    source_key: Text
    positive_signals: list[Annotated[Text, Field(max_length=100)]] = Field(default_factory=list, max_length=3)
    concern_signals: list[Annotated[Text, Field(max_length=100)]] = Field(default_factory=list, max_length=3)
    signals_origin: Literal["SOURCE", "DERIVED"] | None = None

    @model_validator(mode="after")
    def check_signals_and_words(self) -> Self:
        signals = self.positive_signals + self.concern_signals
        if signals and (self.kind != "TALENT" or self.signals_origin is None):
            raise ValueError("신호는 TALENT에만 허용하며 signalsOrigin이 필요합니다.")
        if self.kind != "TALENT" and self.signals_origin is not None:
            raise ValueError("signalsOrigin은 TALENT에만 허용합니다.")
        check_words([self.title, self.content, *signals])
        return self


class Review(Model):
    reviewed_by: Text | None = Field(default=None, max_length=50)
    reviewed_at: date | None = None


class Package(Model):
    schema_version: Literal[1]
    code: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")]
    display_name: Text = Field(max_length=30)
    legal_name: Text = Field(max_length=100)
    aliases: list[Annotated[Text, Field(max_length=30)]] = Field(default_factory=list, max_length=10)
    parent_code: str | None = None
    category: Category
    legal_size: Literal["LARGE", "MID", "SMALL"] | None = None
    legal_size_source: Https | None = None
    dart_corp_code: Annotated[str, StringConstraints(pattern=r"^\d{8}$")] | None = None
    business_reg_no: Annotated[str, StringConstraints(pattern=r"^\d{10}$")] | None = None
    data_as_of: date
    sources: list[Source] = Field(min_length=1, max_length=30)
    facts: list[Fact] = Field(min_length=1, max_length=20)
    interviewer_brief: Text = Field(max_length=600)
    review: Review

    @model_validator(mode="after")
    def check_links(self) -> Self:
        keys = {source.source_key for source in self.sources}
        if len(keys) != len(self.sources) or any(fact.source_key not in keys for fact in self.facts):
            raise ValueError("중복 sourceKey이거나 fact의 출처가 없습니다.")
        kinds = {fact.kind for fact in self.facts}
        if not {"TALENT", "BUSINESS"} <= kinds:
            raise ValueError("BUSINESS와 TALENT가 각각 1개 이상 필요합니다.")
        if self.parent_code == self.code:
            raise ValueError("자기 자신을 모회사로 지정할 수 없습니다.")
        if self.legal_size and not self.legal_size_source:
            raise ValueError("규모 판정에는 legalSizeSource가 필요합니다.")
        if self.data_as_of != max(source.collected_at for source in self.sources):
            raise ValueError("dataAsOf는 가장 늦은 수집일이어야 합니다.")
        check_words([self.interviewer_brief])
        return self


def check_words(texts: list[str]) -> None:
    if any(word in text for text in texts for word in FORBIDDEN):
        raise ValueError("면접 사실 오인·소속 사칭을 유발하는 금지 표현이 있습니다.")


def validate_bundle(directory: Path, *, reviewed: bool, today: date) -> list[Package]:
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError("검사할 기업 패키지가 없습니다.")
    packages = []
    for file in files:
        package = Package.model_validate_json(file.read_text(encoding="utf-8"))
        check_authoring(file, package, reviewed=reviewed, today=today)
        packages.append(package)
    check_bundle_links(packages)
    return packages


def check_authoring(file: Path, package: Package, *, reviewed: bool, today: date) -> None:
    if file.name != f"{package.code}.json":
        raise ValueError(f"파일 이름과 code가 다릅니다: {file.name}")
    if any(source.source_type == "SAMPLE" for source in package.sources):
        raise ValueError(f"실제 기업 자료에 SAMPLE 출처를 허용하지 않습니다: {package.code}")
    if not 2 <= sum(fact.kind == "BUSINESS" for fact in package.facts) <= 4:  # noqa: PLR2004
        raise ValueError(f"대표 서비스는 2~4개여야 합니다: {package.code}")
    for source in package.sources:
        if source.collected_at > today or (source.published_at and source.published_at > source.collected_at):
            raise ValueError(f"출처 날짜가 수집일 또는 기준일 이후입니다: {package.code}/{source.source_key}")
    review = package.review
    if reviewed and (review.reviewed_by is None or review.reviewed_at is None):
        raise ValueError(f"사람 검수 기록이 없습니다: {package.code}")
    if review.reviewed_at and not package.data_as_of <= review.reviewed_at <= today:
        raise ValueError(f"검수일은 수집 기준일 이후·오늘 이하여야 합니다: {package.code}")


def check_bundle_links(packages: list[Package]) -> None:
    for field in ("code", "dart_corp_code", "business_reg_no"):
        values = [value for package in packages if (value := getattr(package, field)) is not None]
        if len(set(values)) != len(values):
            raise ValueError(f"기업 간 {field} 중복입니다.")
    parents = {package.code: package.parent_code for package in packages}
    for package in packages:
        seen: set[str] = set()
        current: str | None = package.code
        while current is not None:
            if current not in parents or current in seen:
                raise ValueError(f"모회사 누락 또는 순환입니다: {package.code}")
            seen.add(current)
            current = parents[current]


def export_bundle(directory: Path, destination: Path, *, today: date) -> None:
    packages = validate_bundle(directory, reviewed=True, today=today)
    destination.mkdir(parents=True, exist_ok=False)
    for package in packages:
        (destination / f"{package.code}.json").write_text(
            json.dumps(package.model_dump(by_alias=True, mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
