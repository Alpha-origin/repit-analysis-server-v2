
from __future__ import annotations

from typing import Any, Protocol


class WebhookClient(Protocol):

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        ...
