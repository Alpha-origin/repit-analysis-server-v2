
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 라이선스/저작권 헤더로 보이는 첫 주석 블록 검출용 키워드.
_LICENSE_KEYWORDS: tuple[str, ...] = ("copyright", "license", "licensed", "spdx-license")

# 연속된 빈 줄 3개 이상을 2개로 줄이는 패턴.
_MULTI_BLANK_PATTERN = re.compile(r"\n{3,}")

# 한 줄 docstring 안에서 트리플 쿼터가 닫혔는지 판정할 때 사용.
_TRIPLE_QUOTE_MIN_COUNT = 2


class Stage4FileReader:

    def __init__(self, max_file_bytes: int, max_files_per_call: int) -> None:
        self._max_file_bytes = max_file_bytes
        self._max_files_per_call = max_files_per_call

    def read_files(
        self,
        paths: list[str],
        path_index: dict[str, str],
        already_read: set[str],
    ) -> dict[str, Any]:
        # 상한 초과 시 앞부터만 처리. 초과분은 over_limit 으로 표기.
        capped_paths = paths[: self._max_files_per_call]
        over_limit = len(paths) - len(capped_paths)

        files: dict[str, dict[str, Any]] = {}
        not_found: list[str] = []
        already_provided: list[str] = []

        for raw_path in capped_paths:
            path = raw_path.strip()
            if not path:
                continue

            if path in already_read:
                # 같은 세션에서 이미 읽은 파일이면 본문은 다시 보내지 않는다.
                already_provided.append(path)
                continue

            abs_path = path_index.get(path)
            if abs_path is None:
                # 경로가 트리에 없으면 not_found 에 모은다. 유사 경로 제안은 v1 미구현.
                not_found.append(path)
                continue

            content, truncated = self._read_and_shrink(Path(abs_path))
            if content is None:
                # 읽기 실패도 not_found 로 묶어 LLM 이 다른 경로를 시도하도록.
                not_found.append(path)
                continue

            files[path] = {"content": content, "truncated": truncated}
            already_read.add(path)

        result: dict[str, Any] = {"files": files}
        if not_found:
            result["not_found"] = not_found
        if already_provided:
            result["already_provided"] = already_provided
        if over_limit > 0:
            result["over_limit_dropped"] = over_limit

        logger.info(
            "stage4_file_reader.handled",
            extra={
                "requested": len(paths),
                "provided": len(files),
                "not_found": len(not_found),
                "already_provided": len(already_provided),
                "over_limit": over_limit,
            },
        )
        return result

    # ---------------- 내부 ----------------

    def _read_and_shrink(self, path: Path) -> tuple[str | None, bool]:
        try:
            raw = path.read_text(errors="ignore")
        except (OSError, UnicodeDecodeError):
            return None, False

        text = _strip_license_header(raw)
        text = _compress_blank_lines(text)
        text, truncated = _truncate_by_bytes(text, self._max_file_bytes)
        return text, truncated


# ---------------- 토큰 절감 헬퍼 ----------------


def _strip_license_header(text: str) -> str:
    lines = text.splitlines()
    start = _find_first_non_blank_line(lines)
    if start is None:
        return text

    first = lines[start].lstrip()
    if first.startswith(("#", "//")):
        end = _find_end_of_line_comment_block(lines, start, prefix=first[:2] if first.startswith("//") else "#")
    elif first.startswith("/*"):
        end = _find_end_of_block_comment(lines, start)
    elif first.startswith('"""') or first.startswith("'''"):
        end = _find_end_of_docstring(lines, start, quote='"""' if first.startswith('"""') else "'''")
    else:
        return text

    block = "\n".join(lines[start:end]).lower()
    if not any(kw in block for kw in _LICENSE_KEYWORDS):
        return text
    return "\n".join(lines[end:]).lstrip("\n")


def _find_first_non_blank_line(lines: list[str]) -> int | None:
    for i, ln in enumerate(lines):
        if ln.strip():
            return i
    return None


def _find_end_of_line_comment_block(lines: list[str], start: int, prefix: str) -> int:
    end = start
    while end < len(lines) and (not lines[end].strip() or lines[end].lstrip().startswith(prefix)):
        end += 1
    return end


def _find_end_of_block_comment(lines: list[str], start: int) -> int:
    end = start
    while end < len(lines) and "*/" not in lines[end]:
        end += 1
    return end + 1


def _find_end_of_docstring(lines: list[str], start: int, quote: str) -> int:
    first = lines[start].lstrip()
    # 같은 줄에서 닫는 트리플 쿼터가 또 나오면 한 줄짜리 docstring.
    if first.count(quote) >= _TRIPLE_QUOTE_MIN_COUNT:
        return start + 1
    end = start + 1
    while end < len(lines) and quote not in lines[end]:
        end += 1
    return end + 1


def _compress_blank_lines(text: str) -> str:
    return _MULTI_BLANK_PATTERN.sub("\n\n", text)


def _truncate_by_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # 멀티바이트 중간을 안전하게 자르기 위해 errors="ignore".
    cut = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return cut + "\n... (이하 생략, 파일이 너무 길어 잘렸음)\n", True
