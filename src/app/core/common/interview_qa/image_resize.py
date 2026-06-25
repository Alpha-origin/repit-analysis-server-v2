
from __future__ import annotations

from io import BytesIO

from PIL import Image


def resize_for_vision(image_bytes: bytes, long_edge_px: int) -> tuple[bytes, str]:
    with Image.open(BytesIO(image_bytes)) as opened:
        # ``thumbnail`` 은 인플레이스 변환이고 in-bound 크기면 아무것도 하지 않는다.
        # Pillow 10+ 에서는 ``Image.Resampling.LANCZOS`` 가 정식 이름이다.
        opened.thumbnail((long_edge_px, long_edge_px), Image.Resampling.LANCZOS)

        # PNG 가 지원하지 않는 일부 모드(CMYK, F 등) 는 RGB 로 정규화.
        # 흔한 RGB / RGBA / P / L 은 그대로 둔다.
        normalized = opened if opened.mode in ("RGB", "RGBA", "P", "L") else opened.convert("RGB")

        out = BytesIO()
        normalized.save(out, format="PNG", optimize=True)
    return out.getvalue(), "image/png"
