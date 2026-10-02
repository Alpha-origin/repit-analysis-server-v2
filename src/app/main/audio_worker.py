"""Run with python -m app.main.audio_worker. One stage at a time per process."""

from __future__ import annotations

import argparse
import asyncio
import logging

from pydantic import ValidationError

from app.core.commands.process_audio_task import ProcessAudioTask
from app.main.audio_config import AudioSettings
from app.main.config import AnthropicSettings, load_callback_security_settings
from app.main.log_redaction import install_log_redaction
from app.main.security_bootstrap import runtime_security_from
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl
from app.outbound.adapters.audio.media_backend import LocalAudioBackend
from app.outbound.adapters.audio.sqlite_repository import SqliteAudioRepository
from app.outbound.adapters.httpx_webhook_client import HttpxWebhookClient


async def run(*, once: bool = False, lane: str | None = None) -> None:
    # production 에서 콜백 토큰이 없으면 DB 를 열거나 작업을 claim 하기 전에 실패한다.
    security = runtime_security_from(load_callback_security_settings())
    settings = AudioSettings()
    repository = SqliteAudioRepository(settings.database_path, settings.max_attempts)
    backend = LocalAudioBackend(settings.source_root, settings.artifact_root)
    try:
        api_key = AnthropicSettings().API_KEY
    except ValidationError:
        api_key = ""
    client = AnthropicTextClientImpl(api_key) if api_key else None
    capacities = settings.capacities()
    if lane:
        capacities = {lane: capacities[lane]}
    processor = ProcessAudioTask(
        repository,
        backend,
        client,
        HttpxWebhookClient(15, 2, security=security),
        capacities,
        settings.lease_seconds,
    )
    try:
        while True:
            processed = await processor.run_once()
            if once:
                return
            if not processed:
                await asyncio.sleep(1)
    finally:
        if client is not None:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--lane", choices=("cpu", "inference", "llm", "callback"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # HTTP request logs can expose presigned S3 query credentials.
    install_log_redaction()
    asyncio.run(run(once=args.once, lane=args.lane))


if __name__ == "__main__":
    main()
