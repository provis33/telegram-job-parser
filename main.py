from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from dotenv import load_dotenv

from database import Database
from handlers import router
from handlers.filters import format_vacancy, vacancy_keyboard
from parsers import hh, linkedin
from parsers.hh import VacancyHit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    hh_user_agent: str
    linkedin_user_agent: str
    parse_interval_minutes: int


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    hh_ua = os.getenv("HH_USER_AGENT")
    li_ua = os.getenv("LINKEDIN_USER_AGENT")
    if not token or not hh_ua or not li_ua:
        raise RuntimeError(
            "Missing env: TELEGRAM_BOT_TOKEN, HH_USER_AGENT, LINKEDIN_USER_AGENT"
        )
    raw = os.getenv("PARSE_INTERVAL_MINUTES", "30")
    try:
        interval = max(1, int(raw))
    except ValueError:
        interval = 30
    return Settings(
        telegram_bot_token=token,
        hh_user_agent=hh_ua,
        linkedin_user_agent=li_ua,
        parse_interval_minutes=interval,
    )


def setup_logging() -> None:
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    file_handler = logging.FileHandler("bot.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)


async def fetch_for_filter(
    settings: Settings,
    keywords: str,
    city: str,
    min_salary: int,
) -> list[VacancyHit]:
    # requests блокирующий — в thread / run sync parsers off-loop
    hh_hits = await asyncio.to_thread(
        hh.search_vacancies,
        keywords=keywords,
        city=city,
        min_salary=min_salary,
        user_agent=settings.hh_user_agent,
    )
    li_hits = await asyncio.to_thread(
        linkedin.search_vacancies,
        keywords=keywords,
        city=city,
        min_salary=min_salary,
        user_agent=settings.linkedin_user_agent,
    )
    return hh_hits + li_hits


async def monitor_loop(bot: Bot, db: Database, settings: Settings) -> None:
    await asyncio.sleep(5)  # дать боту подняться / let bot settle
    while True:
        try:
            await run_parse_cycle(bot, db, settings)
        except Exception:
            logger.exception("Parse cycle failed")
        await asyncio.sleep(settings.parse_interval_minutes * 60)


async def run_parse_cycle(bot: Bot, db: Database, settings: Settings) -> None:
    rows = await db.all_filters_with_tg()
    logger.info("Parse cycle: %s filters", len(rows))
    for tg_id, filt in rows:
        query = filt.keywords.replace(",", " ")
        try:
            hits = await fetch_for_filter(
                settings,
                keywords=query,
                city=filt.city,
                min_salary=filt.min_salary,
            )
        except Exception:
            logger.exception("Fetch failed for filter #%s", filt.id)
            continue

        for hit in hits:
            vacancy = await db.upsert_vacancy(
                source=hit.source,
                external_id=hit.external_id,
                title=hit.title,
                company=hit.company,
                url=hit.url,
                salary_text=hit.salary_text,
                published_at=hit.published_at,
            )
            if await db.was_notified(filt.user_id, vacancy.id):
                continue
            try:
                await bot.send_message(
                    tg_id,
                    format_vacancy(vacancy),
                    parse_mode="HTML",
                    reply_markup=vacancy_keyboard(vacancy.id, vacancy.url),
                    disable_web_page_preview=True,
                )
                await db.mark_notified(filt.user_id, vacancy.id)
            except Exception:
                logger.exception("Send failed tg=%s vacancy=%s", tg_id, vacancy.id)


async def run() -> None:
    setup_logging()
    settings = load_settings()

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    db = Database(Path("data/bot.db"))

    dp["db"] = db
    dp.include_router(router)

    monitor_task: asyncio.Task | None = None

    async def on_startup() -> None:
        nonlocal monitor_task
        await db.connect()
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Инструкция"),
                BotCommand(command="add_filter", description="Добавить фильтр"),
                BotCommand(command="list_filters", description="Список фильтров"),
                BotCommand(command="delete_filter", description="Удалить фильтр"),
                BotCommand(command="favorites", description="Избранные вакансии"),
            ]
        )
        monitor_task = asyncio.create_task(
            monitor_loop(bot, db, settings),
            name="job-monitor",
        )
        logger.info(
            "Bot started, interval=%s min",
            settings.parse_interval_minutes,
        )

    async def on_shutdown() -> None:
        if monitor_task:
            monitor_task.cancel()
        await db.close()
        logger.info("Bot stopped")

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
