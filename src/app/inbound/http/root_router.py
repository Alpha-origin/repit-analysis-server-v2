from fastapi import APIRouter


def make_fastapi_root_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return router
