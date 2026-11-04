# Парсер вакансий

Telegram-бот: мониторит hh.ru (официальное API) и публичные страницы LinkedIn, присылает новые вакансии по фильтрам.

## Важно

- **hh.ru** — используй осмысленный `HH_USER_AGENT` (имя приложения + контактный email), соблюдай [условия API](https://github.com/hhru/api).
- **LinkedIn** — публичный HTML-парсинг легко блокируется. Соблюдай правила сайта, не уменьшай задержки, не делай агрессивный скрейпинг. При блоке бот логирует и продолжает работу (hh.ru всё равно опрашивается).
- Не злоупотребляй частотой: `PARSE_INTERVAL_MINUTES` лучше не ставить ниже 15–30.

## Возможности

- Фильтры: ключевые слова, город, мин. зарплата
- Автопроверка раз в N минут
- Кнопки **Откликнуться** (ссылка) и **В избранное**
- `/favorites` — сохранённые вакансии
- Retry с экспоненциальной задержкой на сетевые ошибки

## Стек

Python 3.11+, aiogram v3, requests, BeautifulSoup4, aiosqlite, python-dotenv.

## Установка

```bash
cd telegram-job-parser
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Заполни `.env` и запусти:

```bash
python main.py
```

Логи — в `bot.log`.

## Переменные окружения

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен от [@BotFather](https://t.me/BotFather) |
| `HH_USER_AGENT` | Например `JobParserBot/1.0 (you@mail.com)` |
| `LINKEDIN_USER_AGENT` | Обычный браузерный User-Agent |
| `PARSE_INTERVAL_MINUTES` | Интервал опроса (по умолчанию `30`) |

## Команды

| Команда | Описание |
|---|---|
| `/start` | Инструкция |
| `/add_filter` | Диалог: ключи → город → мин. зарплата |
| `/list_filters` | Список фильтров |
| `/delete_filter <id>` | Удалить фильтр |
| `/favorites` | Избранные |

## Структура

```
main.py
database.py
handlers/filters.py
parsers/hh.py
parsers/linkedin.py
requirements.txt
.env.example
```
