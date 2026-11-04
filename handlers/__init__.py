from aiogram import Router

from handlers.filters import router as filters_router

router = Router()
router.include_router(filters_router)
