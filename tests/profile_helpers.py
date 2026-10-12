from typing import Any

# 저장소 트리에 실제로 있는 파일. path_index 에는 파일만 들어간다(stage3_repo_tree._walk_repo).
PATH_INDEX = {
    "order-api/build.gradle": "/repo/order-api/build.gradle",
    "order-api/src/main/java/order/StockService.java": "/repo/order-api/StockService.java",
    "order-api/src/main/java/order/RedisLockConfig.java": "/repo/order-api/RedisLockConfig.java",
    "order-api/src/main/java/order/PaymentClient.java": "/repo/order-api/PaymentClient.java",
    "order-web/package.json": "/repo/order-web/package.json",
}


def evidence(path: str, note: str = "확인") -> dict[str, str]:
    return {"path": path, "note": note}


def raw_profile() -> dict[str, Any]:
    """submit_profile 도구 입력 형태(snake_case). 다섯 카테고리의 재료가 모두 있다."""
    return {
        "overview": "주문·재고 서비스",
        "tech_stack": [
            {"name": "Spring Boot", "evidence": [evidence("order-api/build.gradle")]},
            {"name": "Redis", "evidence": [evidence("order-api/src/main/java/order/RedisLockConfig.java")]},
        ],
        "repositories": [
            {"repo": "order-api", "role": "api_server", "description": "주문 API", "tech_stack": ["Spring Boot"]},
        ],
        "core_features": [
            {
                "name": "재고 차감",
                "description": "주문 시 재고를 줄인다",
                "implementation": "분산락으로 동시 차감을 막는다",
                "evidence": [
                    evidence("order-api/src/main/java/order/StockService.java", "차감 로직"),
                    evidence("order-api/src/main/java/order/StockService.java", "중복"),
                ],
            },
        ],
        "troubleshootings": [
            {
                "title": "재고 음수",
                "portfolio_claim": "동시 주문으로 재고가 음수가 됐다",
                "resolution_in_code": "Redisson 락",
                "evidence": [evidence("order-api/src/main/java/order/RedisLockConfig.java")],
            },
        ],
        "integrations": [
            {
                "from": "order-api",
                "to": "결제 API",
                "method": "HTTP",
                "evidence": [evidence("order-api/src/main/java/order/PaymentClient.java")],
            },
        ],
        "claim_checks": [{"claim": "TPS 3배 향상", "status": "unverified", "note": "수치 근거 없음"}],
        "structure_notes": [
            {"repo": "order-api", "summary": "계층형 구조", "key_paths": ["order-api/src/main/java/order/"]},
        ],
    }
