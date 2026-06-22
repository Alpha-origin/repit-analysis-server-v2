"""이미지 리사이즈 헬퍼 — 비전 호출 전 토큰 절감용.

Pillow 로 이미지를 연 뒤 긴 변이 상한을 넘으면 비율 유지하며 줄인다.
다이어그램 안의 텍스트 가독성을 위해 PNG 로 재인코딩.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image


def resize_for_vision(image_bytes: bytes, long_edge_px: int) -> tuple[bytes, str]:
    """비전 모델에 보낼 이미지를 만든다.

    Args:
        image_bytes: 원본 이미지 바이트(fitz 가 추출한 형식 그대로).
        long_edge_px: 결과 이미지의 긴 변 픽셀 상한. 이미 작으면 줄이지 않는다.

    Returns:
        ``(재인코딩된 PNG 바이트, "image/png")``.

    PNG 로 통일하는 이유: 아키텍처 다이어그램·차트 안 텍스트가 JPEG 압축으로
    뭉개지면 모델 인식률이 떨어진다. 다이어그램은 색 수가 적어 PNG 가 충분히 작다.
    """
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
