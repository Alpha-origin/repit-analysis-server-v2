"""GitHub 저장소 트리 생성에서 사용하는 블랙리스트.

명백히 분석 가치가 없는 디렉터리/파일을 제외해 LLM 탐색이 의미 있는 코드만
보게 한다. 단, "구조 파악용 디렉터리 골격" 자체는 남도록 트리에는 디렉터리 이름이
경로 일부로 그대로 노출된다.

이 모듈은 env 가 아닌 코드 상수로만 유지한다(목록이 길고, 변경 빈도가 매우 낮음).
"""

from __future__ import annotations

# 빌드 산출물·IDE 설정·캐시·종속성·vcs 메타 등을 통째로 가지치기한다.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        "build",
        "target",
        ".gradle",
        "node_modules",
        "dist",
        "out",
        ".git",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        "venv",
        ".venv",
        "coverage",
        ".next",
        "vendor",
    }
)

# 이진 파일·자산·미니파이·잠금파일 등. 본문 분석에 도움이 안 되거나 거대한 것들.
# 다단계 확장자(예: ``.min.js``) 도 포함하기 때문에 endswith 로 매칭한다.
SKIP_EXTS: frozenset[str] = frozenset(
    {
        ".lock",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".class",
        ".jar",
        ".war",
        ".min.js",
        ".map",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".pyc",
        ".mp4",
        ".mp3",
        ".webp",
        ".bin",
        ".so",
        ".dll",
    }
)

# 파일명 그대로 매칭(잠금파일·OS 메타데이터 등).
SKIP_FILES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Pipfile.lock",
        "go.sum",
        ".DS_Store",
    }
)
