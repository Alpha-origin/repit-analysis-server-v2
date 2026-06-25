
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
from pathlib import Path

from app.core.common.interview_qa.constants import SKIP_DIRS, SKIP_EXTS, SKIP_FILES
from app.core.common.interview_qa.dto import RepoMeta, RepoRole, RepoTree, Stage3Result
from app.core.common.interview_qa.ports.github_tarball_fetcher import (
    GithubTarballFetcher,
    GithubTarballFetcherError,
)

logger = logging.getLogger(__name__)


# 역할 판정용 키워드(레포 이름 토큰 매칭).
_NAME_KEYWORDS: tuple[tuple[tuple[str, ...], RepoRole], ...] = (
    (("ai", "ml", "infer", "model"), "ai_server"),
    (("api", "server", "backend"), "api_server"),
    (("web", "front", "client", "ui"), "frontend"),
    (("infra", "deploy", "ops"), "infra"),
)

# 파이썬 ai 의존성 시그널(요청한 키워드 목록).
_AI_PY_DEP_KEYWORDS: tuple[str, ...] = ("torch", "transformers", "tensorflow", "langchain", "openai")

# package.json frontend 시그널(키 이름 매칭).
_FRONTEND_NODE_DEPS: tuple[str, ...] = ("react", "vue", "next", "@angular/core")

# 파이썬 의존성을 읽을 후보 파일.
_PY_DEP_FILES: tuple[str, ...] = ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg")


class Stage3RepoTree:

    def __init__(self, fetcher: GithubTarballFetcher) -> None:
        # tarball 다운로드는 어댑터에 위임. tar 해제·파일 시스템 작업은 이 서비스가 직접 한다.
        self._fetcher = fetcher

    async def execute(self, repos: tuple[RepoMeta, ...], working_dir: str) -> Stage3Result:
        results: list[RepoTree] = []
        path_index: dict[str, str] = {}

        # 저장소들은 보통 2~5개 정도라 직렬 처리로 충분(다운로드 + 해제 I/O 부담).
        for repo in repos:
            tree, index = await self._process_repo(repo, working_dir)
            results.append(tree)
            path_index.update(index)

        tree_text = _render_tree_text(results)

        logger.info(
            "stage3_repo_tree.done",
            extra={
                "repo_count": len(results),
                "repos": [{"name": r.name, "role": r.role, "files": len(r.file_paths)} for r in results],
                "tree_lines": tree_text.count("\n") + (1 if tree_text else 0),
                "path_index_size": len(path_index),
            },
        )
        return Stage3Result(repos=results, tree_text=tree_text, path_index=path_index)

    # ---------------- 저장소 1개 처리 ----------------

    async def _process_repo(self, repo: RepoMeta, working_dir: str) -> tuple[RepoTree, dict[str, str]]:
        try:
            tar_bytes = await self._fetcher.fetch(repo.owner, repo.name, repo.default_branch)
        except GithubTarballFetcherError:
            # 빈 트리/인덱스로 진행. 다른 저장소는 계속 처리.
            logger.warning("stage3_repo_tree.fetch_failed", extra={"repo": repo.name})
            return RepoTree(name=repo.name, role="unknown", file_paths=[]), {}

        # tarfile 해제 + 트리 수집은 CPU/디스크 I/O 라 워커 스레드로.
        repo_root = await asyncio.to_thread(_extract_tarball, tar_bytes, working_dir, repo.name)

        role = _detect_role(repo.name, repo_root)
        file_paths, index = _walk_repo(repo_root, repo.name)

        return RepoTree(name=repo.name, role=role, file_paths=file_paths), index


# ---------------- 모듈 헬퍼 (순수 동기) ----------------


def _extract_tarball(tar_bytes: bytes, working_dir: str, repo_name: str) -> Path:
    dest = Path(working_dir) / f"{repo_name}_extracted"
    dest.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        # ``filter="data"`` (Python 3.12+) 는 절대 경로/심볼릭 링크/상위 경로 traversal 을 차단한다.
        tar.extractall(dest, filter="data")

    # 단일 최상위 디렉터리를 찾는다. 여러 개면 첫 번째 디렉터리를 사용.
    children = [c for c in dest.iterdir() if c.is_dir()]
    if not children:
        # 빈 tarball 인 경우 dest 를 그대로 루트로 본다.
        return dest
    return children[0]


def _tokenize_repo_name(name: str) -> set[str]:
    # 1차: 구분자(- _ .) 분리.
    parts = re.split(r"[-_.\s]+", name)
    tokens: set[str] = set()
    for part in parts:
        # camelCase / PascalCase 를 공백으로 분리.
        with_space = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", part)
        with_space = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", with_space)
        for token in with_space.split():
            if token:
                tokens.add(token.lower())
    return tokens


def _detect_role(repo_name: str, repo_root: Path) -> RepoRole:
    tokens = _tokenize_repo_name(repo_name)

    # 1차: 이름 토큰.
    for keywords, role in _NAME_KEYWORDS:
        if any(kw in tokens for kw in keywords):
            return role

    # 2차: 의존성 파일.
    py_text = _read_python_deps(repo_root).lower()
    if any(kw in py_text for kw in _AI_PY_DEP_KEYWORDS):
        return "ai_server"

    # JVM 백엔드 시그널.
    for jvm_file in ("build.gradle", "build.gradle.kts", "pom.xml"):
        if (repo_root / jvm_file).exists():
            return "api_server"

    # Node 기반 — frontend / 일반 node 백엔드.
    pkg_path = repo_root / "package.json"
    if pkg_path.exists():
        all_deps = _read_node_deps(pkg_path)
        if any(kw in all_deps for kw in _FRONTEND_NODE_DEPS):
            return "frontend"
        # node 인데 frontend 아니면 일반 백엔드로 본다.
        return "api_server"

    return "unknown"


def _read_python_deps(repo_root: Path) -> str:
    contents: list[str] = []
    for name in _PY_DEP_FILES:
        path = repo_root / name
        if not path.exists():
            continue
        try:
            contents.append(path.read_text(errors="ignore"))
        except OSError:
            continue
    return "\n".join(contents)


def _read_node_deps(pkg_path: Path) -> set[str]:
    try:
        data = json.loads(pkg_path.read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update(str(k) for k in section)
    return deps


def _walk_repo(repo_root: Path, repo_name: str) -> tuple[list[str], dict[str, str]]:
    rel_paths: list[str] = []
    index: dict[str, str] = {}

    # ``Path.walk`` 는 Python 3.12+ 의 표준 API.
    for dirpath, dirnames, filenames in repo_root.walk():
        # SKIP_DIRS 를 제자리(in-place) 에서 제거해 그 하위로 들어가지 않게 한다.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            if filename in SKIP_FILES:
                continue
            if _ext_skipped(filename):
                continue

            abs_path = dirpath / filename
            rel = abs_path.relative_to(repo_root).as_posix()
            rel_paths.append(rel)
            index[f"{repo_name}/{rel}"] = str(abs_path)

    rel_paths.sort()
    return rel_paths, index


def _ext_skipped(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in SKIP_EXTS)


def _render_tree_text(repos: list[RepoTree]) -> str:
    blocks: list[str] = []
    for repo in repos:
        lines = [f"[{repo.role}] {repo.name}"]
        lines.extend(f"  {path}" for path in repo.file_paths)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
