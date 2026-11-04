from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database import Database, VacancyRow

logger = logging.getLogger(__name__)
router = Router()


class AddFilter(StatesGroup):
    keywords = State()
    city = State()
    min_salary = State()


def vacancy_keyboard(vacancy_id: int, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Откликнуться", url=url),
                InlineKeyboardButton(text="В избранное", callback_data=f"fav:{vacancy_id}"),
            ]
        ]
    )


def format_vacancy(v: VacancyRow) -> str:
    company = v.company or "—"
    salary = v.salary_text or "не указана"
    return (
        f"<b>{v.title}</b>\n"
        f"🏢 {company}\n"
        f"💰 {salary}\n"
        f"Источник: {v.source}\n"
        f"📅 {v.published_at or '—'}"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    await db.ensure_user(message.from_user.id)
    await message.answer(
        "Привет! Я парсер вакансий (hh.ru API + публичный LinkedIn).\n\n"
        "Команды:\n"
        "/add_filter — добавить фильтр поиска\n"
        "/list_filters — список фильтров\n"
        "/delete_filter &lt;id&gt; — удалить фильтр\n"
        "/favorites — избранные вакансии\n\n"
        "Новые вакансии приходят автоматически по расписанию."
    )


@router.message(Command("add_filter"))
async def cmd_add_filter(message: Message, state: FSMContext) -> None:
    await state.set_state(AddFilter.keywords)
    await message.answer(
        "Ключевые слова через запятую?\n"
        "Например: <code>python, backend, django</code>",
        parse_mode="HTML",
    )


@router.message(AddFilter.keywords)
async def add_keywords(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        await message.answer("Нужно хотя бы одно ключевое слово.")
        return
    keywords = ", ".join(parts)
    await state.update_data(keywords=keywords)
    await state.set_state(AddFilter.city)
    await message.answer("Город? (например: Москва или Berlin)")


@router.message(AddFilter.city)
async def add_city(message: Message, state: FSMContext) -> None:
    city = (message.text or "").strip()
    if len(city) < 2:
        await message.answer("Слишком короткое название города.")
        return
    await state.update_data(city=city)
    await state.set_state(AddFilter.min_salary)
    await message.answer("Минимальная зарплата числом? (0 — без фильтра)")


@router.message(AddFilter.min_salary)
async def add_salary(message: Message, state: FSMContext, db: Database) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d+", raw):
        await message.answer("Введи целое число, например 150000 или 0.")
        return
    min_salary = int(raw)
    data = await state.get_data()
    await state.clear()

    user_id = await db.ensure_user(message.from_user.id)
    filter_id = await db.add_filter(
        user_id=user_id,
        keywords=data["keywords"],
        city=data["city"],
        min_salary=min_salary,
    )
    await message.answer(
        f"Фильтр #{filter_id} сохранён.\n"
        f"Ключи: {data['keywords']}\n"
        f"Город: {data['city']}\n"
        f"Мин. зарплата: {min_salary}"
    )


@router.message(Command("list_filters"))
async def cmd_list_filters(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    user_id = await db.ensure_user(message.from_user.id)
    rows = await db.list_filters(user_id)
    if not rows:
        await message.answer("Фильтров нет. Добавь через /add_filter")
        return
    lines = [
        f"#{r.id}: {r.keywords} · {r.city} · min {r.min_salary}"
        for r in rows
    ]
    await message.answer("Твои фильтры:\n" + "\n".join(lines))


@router.message(Command("delete_filter"))
async def cmd_delete_filter(
    message: Message,
    command: CommandObject,
    db: Database,
    state: FSMContext,
) -> None:
    await state.clear()
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /delete_filter &lt;id&gt;", parse_mode="HTML")
        return
    filter_id = int(command.args.strip())
    user_id = await db.ensure_user(message.from_user.id)
    ok = await db.delete_filter(filter_id, user_id)
    if ok:
        await message.answer(f"Фильтр #{filter_id} удалён.")
    else:
        await message.answer("Фильтр не найден.")


@router.message(Command("favorites"))
async def cmd_favorites(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    user_id = await db.ensure_user(message.from_user.id)
    favs = await db.list_favorites(user_id)
    if not favs:
        await message.answer("Избранное пусто.")
        return
    await message.answer(f"Избранных: {len(favs)}")
    for v in favs[:30]:
        await message.answer(
            format_vacancy(v),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Открыть", url=v.url)]]
            ),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data.startswith("fav:"))
async def cb_favorite(callback: CallbackQuery, db: Database) -> None:
    vacancy_id = int(callback.data.split(":", 1)[1])
    user_id = await db.ensure_user(callback.from_user.id)
    vacancy = await db.get_vacancy(vacancy_id)
    if vacancy is None:
        await callback.answer("Вакансия не найдена", show_alert=True)
        return
    added = await db.add_favorite(user_id, vacancy_id)
    if added:
        await callback.answer("В избранном")
    else:
        await callback.answer("Уже в избранном")
