from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.common.applicant_profile.dto_v2 import ApplicantProfileV2, DraftProfileV2, SourceV2
from app.core.common.applicant_profile.prompt_v2 import TOOL, TOOL_NAME, system_prompt, system_prompt_general
from app.core.common.applicant_profile.refine_v2 import ProfileEvidenceRefineV2
from app.core.common.applicant_profile.tools import PROFILE_TOOLS
from app.core.common.interview_qa.dto import Stage3Result
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.exploration_session import ExplorationSession, ExplorationSpec
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClientError
from app.core.common.interview_qa.ports.pdf_fetcher import PdfFetcher, PdfFetcherError
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_nondev.dto import (
    DocumentReading,
    ProfileCoverLetter,
    ProfileDocument,
    ProfileSupplement,
)
from app.core.common.interview_qa.stage2_nondev.extract import DocumentExtract
from app.core.common.interview_qa.stage2_nondev.masking import mask_text
from app.core.common.interview_qa.stage2_nondev.merge import (
    cover_letter_blocks,
    format_cover_letters,
    format_supplements,
    page_text,
    sources,
    supplement_blocks,
)
from app.core.common.interview_qa.stage2_nondev.page_reader import PageReader
from app.core.common.interview_qa.stage2_nondev.ports import DocumentModelClient
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.job_spec import JobSpec
from app.core.common.prompt_boundary import job_title_block
from app.core.common.tool_use import extract_tool_input

DATA_SUFFIXES = frozenset({".csv", ".tsv", ".parquet", ".pkl", ".h5", ".xlsx", ".npy", ".feather", ".db", ".sqlite"})


def _sensitive_path(path: str) -> bool:
    # 파일 시스템에 따라 대소문자만 다른 이름(.ENV, ID_RSA, SERVER.PEM)도 같은 파일이므로 소문자로 비교한다.
    return any(
        part.startswith((".env", "id_rsa")) or part.endswith((".tfstate", ".tfvars", ".tfvars.json", ".pem", ".key"))
        for part in (raw.lower() for raw in Path(path).parts)
    )


def filter_nondev_tree(tree: Stage3Result) -> Stage3Result:
    repos = [
        repo.model_copy(update={"file_paths": [path for path in repo.file_paths if not _sensitive_path(path)]})
        for repo in tree.repos
    ]
    index = {
        path: actual
        for path, actual in tree.path_index.items()
        if not _sensitive_path(path) and Path(path).suffix.lower() not in DATA_SUFFIXES
    }
    text = "\n".join(f"[{repo.name} · {repo.role}]\n" + "\n".join(repo.file_paths) for repo in repos)
    return Stage3Result(repos=repos, tree_text=text, path_index=index)


class UnreadableDocumentError(PipelineError):
    def __init__(self) -> None:
        super().__init__(422, "필수 문서에서 읽을 내용을 찾지 못했습니다. 해상도와 문서 내용을 확인해 주세요.")


class NondevFileReader(Stage4FileReader):
    def _read_and_shrink(self, path: Path) -> tuple[str | None, bool]:
        if path.suffix.lower() in DATA_SUFFIXES:
            return None, False
        if path.suffix.lower() != ".ipynb":
            text, truncated = super()._read_and_shrink(path)
            return (mask_text(text) if text is not None else None), truncated
        try:
            notebook = json.loads(path.read_text())
            text = "\n\n".join(
                "".join(cell.get("source", []))
                for cell in notebook["cells"]
                if cell.get("cell_type") in {"code", "markdown"}
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None, False
        encoded = mask_text(text).encode()
        return encoded[: self._max_file_bytes].decode(errors="ignore"), len(encoded) > self._max_file_bytes


class DocumentProfileBuild:
    def __init__(
        self,
        client: DocumentModelClient,
        fetcher: PdfFetcher,
        extractor: DocumentExtract,
        reader: PageReader,
        stage1: Stage1Validation,
        stage3: Stage3RepoTree,
        file_reader: Stage4FileReader,
        model: str,
        input_token_limit: int = 100_000,
        response_max_tokens: int = 8192,
        max_turns: int = 8,
    ) -> None:
        self.client, self.fetcher, self.extractor, self.reader = client, fetcher, extractor, reader
        self.stage1, self.stage3, self.file_reader = stage1, stage3, file_reader
        self.model, self.input_token_limit = model, input_token_limit
        self.response_max_tokens, self.max_turns = response_max_tokens, max_turns

    async def execute(
        self,
        job: JobSpec,
        documents: tuple[ProfileDocument, ...],
        supplements: tuple[ProfileSupplement, ...],
        github_urls: tuple[str, ...],
        *,
        cover_letters: tuple[ProfileCoverLetter, ...] = (),
        job_title: str | None = None,
    ) -> ApplicantProfileV2:
        inputs = []
        for index, document in enumerate(documents, 1):
            try:
                inputs.append((document, await self.fetcher.fetch(document.url)))
            except PdfFetcherError as exc:
                raise PipelineError(422, f"doc-{index}: PDF를 내려받지 못했습니다.") from exc
        repos = await self.stage1._validate_repos(github_urls)
        if inputs:
            extracted = await self.extractor.execute(inputs)
            reading = await self.reader.execute(extracted)
        else:
            reading = DocumentReading([])
        self._ensure_readable(reading, job, cover_letters)
        source_list = sources(reading, supplements, cover_letters)
        system = system_prompt_general(job) if job.family == "GENERAL" else system_prompt(job)
        # 직무명은 지원자가 입력한 값이라 시스템 프롬프트가 아니라 사용자 메시지의 경계 태그로만 보낸다.
        title = job_title_block(job_title) + "\n" if job.family == "GENERAL" and job_title else ""
        text, dropped = await self._budget(reading, supplements, job, system, cover_letters=cover_letters)
        sources_by_id = {source.id: source for source in source_list}
        for document_id, page in sorted(dropped):
            sources_by_id[document_id].dropped_pages.append(page)
        with tempfile.TemporaryDirectory(prefix="nondev_profile_") as directory:
            tree = await self.stage3.execute(repos=tuple(repos), working_dir=directory) if repos else None
            if tree is not None:
                tree = filter_nondev_tree(tree)
                source_list.extend(
                    SourceV2(id=f"repo:{repo.name}", kind="REPOSITORY", title=repo.name) for repo in tree.repos
                )
                # Tree labels are context too; count them before allowing any model generation.
                full_text = text + "\n[저장소 트리]\n" + tree.tree_text
                if await self._count(system, full_text, with_code=True) > self.input_token_limit:
                    text, dropped = await self._budget(
                        reading, supplements, job, system, tree.tree_text, cover_letters=cover_letters
                    )
                    for source in source_list:
                        source.dropped_pages = sorted({page for doc, page in dropped if doc == source.id})
                text += "\n[저장소 트리]\n" + tree.tree_text
                spec = ExplorationSpec(
                    system,
                    [READ_FILES_TOOL, TOOL],
                    TOOL_NAME,
                    "문서 종합 데이터 생성에 실패했습니다.",
                    "nondev.profile",
                )
                raw = await ExplorationSession(
                    self.client,
                    self.file_reader,
                    self.model,
                    self.max_turns,
                    self.input_token_limit,
                    self.response_max_tokens,
                    spec,
                ).execute(title + text, tree.path_index)
            else:
                raw = await self._generate(system, title + text)
            try:
                draft = DraftProfileV2.model_validate(raw)
            except ValidationError as exc:
                raise PipelineError(500, "문서 종합 데이터 형식이 올바르지 않습니다.") from exc
            blocks = (
                [
                    block
                    for block in reading.blocks
                    if (block.path.split("/")[0], int(block.path.split("/p")[1])) not in dropped
                ]
                + supplement_blocks(supplements)
                + cover_letter_blocks(cover_letters)
            )
            profile = ProfileEvidenceRefineV2().execute(
                draft, job, blocks, source_list, tree.path_index if tree else {}
            )
            return profile.model_copy(update={"job_title": job_title}) if job.family == "GENERAL" else profile

    @staticmethod
    def _ensure_readable(
        reading: DocumentReading, job: JobSpec, cover_letters: tuple[ProfileCoverLetter, ...] = ()
    ) -> None:
        if job.material_rule == "NON_RESUME_OR_COVER_LETTER" and cover_letters:
            return
        for document in reading.documents:
            if job.material_rule == "REQUIRED_TYPE":
                if document.type not in job.required_docs:
                    continue
            elif document.type == "RESUME":
                continue
            direct = "".join(
                block.text
                for page in document.pages
                for block in page.blocks
                if block.method == "TABLE" or (block.method == "TEXT" and page.kind != "E")
            )
            vision = any(
                block.text.strip()
                for page in document.pages
                for block in page.blocks
                if block.method == "VISION_TRANSCRIPT"
            )
            # 읽기 요약은 인용 근거가 아니므로 읽을 수 있는 문서로 세지 않는다.
            if len(direct.strip()) >= 50 or vision:  # noqa: PLR2004
                return
        raise UnreadableDocumentError()

    async def _budget(
        self,
        reading: DocumentReading,
        supplements: tuple[ProfileSupplement, ...],
        job: JobSpec,
        system: str,
        tree_text: str = "",
        *,
        cover_letters: tuple[ProfileCoverLetter, ...] = (),
    ) -> tuple[str, set[tuple[str, int]]]:
        pages = {
            (document.id, page.number): page_text(document, page)
            + "\n"
            + "\n".join(
                f"[읽기 요약 · 인용 불가] {summary}"
                for summary in sorted(set(reading.summaries.get(f"{document.id}/p{page.number}", [])))
            )
            for document in reading.documents
            for page in document.pages
        }
        supplement_text = format_supplements(supplements)
        if cover_letters:
            supplement_text += "\n" + format_cover_letters(cover_letters)
        # Stable document order and trailing pages; required document types are removed last.
        ordered = sorted(
            reading.documents,
            key=lambda document: (
                0
                if document.type == "RESUME"
                else 3
                if document.type in job.required_docs
                else 1
                if document.type == "CAREER_DESCRIPTION"
                else 2,
            ),
        )
        dropped: set[tuple[str, int]] = set()
        while True:
            text = "\n".join(value for key, value in pages.items() if key not in dropped) + "\n" + supplement_text
            count_text = text + ("\n[저장소 트리]\n" + tree_text if tree_text else "")
            if await self._count(system, count_text, with_code=bool(tree_text)) <= self.input_token_limit:
                return text, dropped
            candidate = next(
                (
                    (document.id, page.number)
                    for document in ordered
                    for page in reversed(document.pages)
                    if (document.id, page.number) not in dropped
                ),
                None,
            )
            if candidate is None:
                raise PipelineError(422, "보완 입력·저장소 정보가 종합 데이터 입력 한도를 넘었습니다.")
            dropped.add(candidate)

    async def _count(self, system: str, text: str, *, with_code: bool = False) -> int:
        try:
            return await self.client.count_tokens(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": text}],
                tools=[READ_FILES_TOOL, TOOL] if with_code else [TOOL],
            )
        except AnthropicTextClientError as exc:
            raise PipelineError(502, "문서 입력량을 확인하지 못했습니다.") from exc

    async def _generate(self, system: str, text: str) -> dict[str, Any]:
        try:
            response = await self.client.call(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": text}],
                tools=[TOOL],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                max_tokens=self.response_max_tokens,
            )
        except AnthropicTextClientError as exc:
            raise PipelineError(502, "문서 종합 데이터 생성에 실패했습니다.") from exc
        raw = extract_tool_input(response.content_blocks, TOOL_NAME)
        if raw is None or response.stop_reason in {"max_tokens", "refusal"}:
            raise PipelineError(502, "문서 종합 데이터 결과가 없거나 잘렸습니다.")
        return raw


# Reuse the existing read_files schema without including the developer-only submit_profile tool.
READ_FILES_TOOL = next(tool for tool in PROFILE_TOOLS if tool["name"] == "read_files")
