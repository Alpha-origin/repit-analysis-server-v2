
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
