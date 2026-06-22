"""4단계 ``read_files`` 도구의 본문 처리.

LLM 이 요청한 경로 목록을 받아 다음을 수행한다:
- 유효 경로 검증(파일 트리 path_index 에 있는지).
- 이미 같은 세션에서 제공된 파일은 본문 대신 ``"이미 제공됨"`` 표기로 응답해
  같은 코드가 messages 에 두 번 쌓이는 것을 방지(토큰 절감).
- 요청 수가 상한(``MAX_FILES_PER_CALL``) 을 넘으면 앞부터만 처리.
- 파일 본문은 다음과 같이 정리해 보낸다.
  - 라이선스 헤더(맨 위 Copyright/License 주석 블록) 제거.
  - 연속된 빈 줄을 최대 2줄로 정리.
  - ``MAX_FILE_BYTES`` 초과 시 잘라 "(이하 생략)" 표기.
- 없는 경로는 ``not_found`` 에 모아 별도 반환.

결과 dict 는 LLM 의 tool_result content 로 그대로 JSON 직렬화된다.
"""

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
    """``read_files`` 도구 요청 → tool_result 본문(dict) 변환."""

    def __init__(self, max_file_bytes: int, max_files_per_call: int) -> None:
        self._max_file_bytes = max_file_bytes
        self._max_files_per_call = max_files_per_call

    def read_files(
        self,
        paths: list[str],
        path_index: dict[str, str],
        already_read: set[str],
    ) -> dict[str, Any]:
        """경로 목록을 처리해 tool_result 본문(dict) 을 만든다.

        ``already_read`` 는 호출자가 관리하는 set 으로, 처리 후 새로 읽은 경로를 직접 update 한다.
        """
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
        """파일을 텍스트로 읽고 토큰 절감 규칙 적용. (text, truncated) 반환."""
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
    """파일 최상단 주석/docstring 블록이 라이선스/저작권을 담고 있으면 통째로 제거.

    파이썬 ``#``, C 계열 ``//``, 블록 주석 ``/* */``, 파이썬 docstring(트리플 따옴표)
    네 가지 형태를 본다. 어느 것이든 키워드가 검출되지 않으면 원본을 그대로 둔다.
    """
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
    """``# ...`` 또는 ``// ...`` 같은 줄 단위 주석의 연속 끝(exclusive) 을 돌려준다."""
    end = start
    while end < len(lines) and (not lines[end].strip() or lines[end].lstrip().startswith(prefix)):
        end += 1
    return end


def _find_end_of_block_comment(lines: list[str], start: int) -> int:
    """``/* ... */`` 블록 주석의 닫는 줄 다음 인덱스를 돌려준다."""
    end = start
    while end < len(lines) and "*/" not in lines[end]:
        end += 1
    return end + 1


def _find_end_of_docstring(lines: list[str], start: int, quote: str) -> int:
    """파이썬 docstring 의 닫는 줄 다음 인덱스를 돌려준다."""
    first = lines[start].lstrip()
    # 같은 줄에서 닫는 트리플 쿼터가 또 나오면 한 줄짜리 docstring.
    if first.count(quote) >= _TRIPLE_QUOTE_MIN_COUNT:
        return start + 1
    end = start + 1
    while end < len(lines) and quote not in lines[end]:
        end += 1
    return end + 1


def _compress_blank_lines(text: str) -> str:
    """연속된 빈 줄 3개 이상을 2개로 줄인다."""
    return _MULTI_BLANK_PATTERN.sub("\n\n", text)


def _truncate_by_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    """UTF-8 바이트 기준으로 잘라낸다. 잘렸으면 표시 줄을 마지막에 붙인다."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # 멀티바이트 중간을 안전하게 자르기 위해 errors="ignore".
    cut = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return cut + "\n... (이하 생략, 파일이 너무 길어 잘렸음)\n", True
