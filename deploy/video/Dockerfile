FROM ghcr.io/astral-sh/uv:0.11.21 AS uv

FROM python:3.12-slim-trixie AS runtime
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 repit \
    && useradd --uid 10001 --gid repit --no-create-home repit \
    && mkdir -p /srv/repit/video \
    && chown repit:repit /srv/repit/video
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY evaluation/behavior/prepare_release.py ./evaluation/behavior/prepare_release.py
COPY deploy/video/logging.json ./deploy/video/logging.json
RUN uv sync --frozen --no-dev
USER 10001:10001
CMD ["uvicorn", "app.main.run:make_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/app/deploy/video/logging.json"]

FROM runtime AS cpu
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libegl1 libgles2 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --extra behavior \
    && python -c "import mediapipe; import av" \
    && repit-behavior-analyzer --help
USER 10001:10001
CMD ["python", "-m", "app.main.video_worker", "--lane", "cpu"]
