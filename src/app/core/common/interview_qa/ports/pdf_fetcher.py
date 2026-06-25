
from __future__ import annotations

from typing import Protocol


class PdfFetcherError(Exception):
    pass

class PdfFetcher(Protocol):

    async def fetch(self, url: str) -> bytes:
        ...
