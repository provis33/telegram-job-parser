from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class FilterRow:
    id: int
    user_id: int
    keywords: str
    city: str
    min_salary: int


@dataclass
class VacancyRow:
    id: int
    source: str
    external_id: str
    title: str
    company: str
    url: str
    salary_text: str
    published_at: str


class Database:
    def __init__(self, db_path: str | Path = "data/bot.db") -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                keywords TEXT NOT NULL,
                city TEXT NOT NULL,
                min_salary INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS vacancies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                salary_text TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                UNIQUE(source, external_id)
            );

            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                vacancy_id INTEGER NOT NULL,
                UNIQUE(user_id, vacancy_id),
                FOREIGN KEY (vacancy_id) REFERENCES vacancies(id)
            );

            CREATE TABLE IF NOT EXISTS notified (
                user_id INTEGER NOT NULL,
                vacancy_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, vacancy_id)
            );
            """
        )
        await self._conn.commit()
        logger.info("SQLite ready: %s", self.db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def ensure_user(self, tg_id: int) -> int:
        cursor = await self.conn.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        row = await cursor.fetchone()
        if row:
            return int(row["id"])
        cursor = await self.conn.execute("INSERT INTO users (tg_id) VALUES (?)", (tg_id,))
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def add_filter(self, user_id: int, keywords: str, city: str, min_salary: int) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO filters (user_id, keywords, city, min_salary)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, keywords, city, min_salary),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def list_filters(self, user_id: int) -> list[FilterRow]:
        cursor = await self.conn.execute(
            "SELECT * FROM filters WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            FilterRow(
                id=r["id"],
                user_id=r["user_id"],
                keywords=r["keywords"],
                city=r["city"],
                min_salary=int(r["min_salary"]),
            )
            for r in rows
        ]

    async def delete_filter(self, filter_id: int, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM filters WHERE id = ? AND user_id = ?",
            (filter_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def all_filters_with_tg(self) -> list[tuple[int, FilterRow]]:
        cursor = await self.conn.execute(
            """
            SELECT f.*, u.tg_id
            FROM filters f
            JOIN users u ON u.id = f.user_id
            """
        )
        rows = await cursor.fetchall()
        result: list[tuple[int, FilterRow]] = []
        for r in rows:
            result.append(
                (
                    int(r["tg_id"]),
                    FilterRow(
                        id=r["id"],
                        user_id=r["user_id"],
                        keywords=r["keywords"],
                        city=r["city"],
                        min_salary=int(r["min_salary"]),
                    ),
                )
            )
        return result

    async def upsert_vacancy(
        self,
        *,
        source: str,
        external_id: str,
        title: str,
        company: str,
        url: str,
        salary_text: str,
        published_at: str,
    ) -> VacancyRow:
        await self.conn.execute(
            """
            INSERT INTO vacancies
                (source, external_id, title, company, url, salary_text, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                url = excluded.url,
                salary_text = excluded.salary_text,
                published_at = excluded.published_at
            """,
            (source, external_id, title, company, url, salary_text, published_at),
        )
        await self.conn.commit()
        cursor = await self.conn.execute(
            "SELECT * FROM vacancies WHERE source = ? AND external_id = ?",
            (source, external_id),
        )
        row = await cursor.fetchone()
        return VacancyRow(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            title=row["title"],
            company=row["company"],
            url=row["url"],
            salary_text=row["salary_text"],
            published_at=row["published_at"],
        )

    async def was_notified(self, user_id: int, vacancy_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM notified WHERE user_id = ? AND vacancy_id = ?",
            (user_id, vacancy_id),
        )
        return await cursor.fetchone() is not None

    async def mark_notified(self, user_id: int, vacancy_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO notified (user_id, vacancy_id) VALUES (?, ?)",
            (user_id, vacancy_id),
        )
        await self.conn.commit()

    async def add_favorite(self, user_id: int, vacancy_id: int) -> bool:
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, vacancy_id) VALUES (?, ?)",
            (user_id, vacancy_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def list_favorites(self, user_id: int) -> list[VacancyRow]:
        cursor = await self.conn.execute(
            """
            SELECT v.* FROM favorites f
            JOIN vacancies v ON v.id = f.vacancy_id
            WHERE f.user_id = ?
            ORDER BY f.id DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            VacancyRow(
                id=r["id"],
                source=r["source"],
                external_id=r["external_id"],
                title=r["title"],
                company=r["company"],
                url=r["url"],
                salary_text=r["salary_text"],
                published_at=r["published_at"],
            )
            for r in rows
        ]

    async def get_vacancy(self, vacancy_id: int) -> VacancyRow | None:
        cursor = await self.conn.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return VacancyRow(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            title=row["title"],
            company=row["company"],
            url=row["url"],
            salary_text=row["salary_text"],
            published_at=row["published_at"],
        )
