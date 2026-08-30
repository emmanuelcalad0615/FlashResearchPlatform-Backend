from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok"}

@router.get("/")
async def root():
    return {"message": "Flash Research API is running"}
