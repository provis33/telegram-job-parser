from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

HH_API = "https://api.hh.ru/vacancies"
HH_AREAS = "https://api.hh.ru/suggests/areas"
MAX_RETRIES = 4


@dataclass
class VacancyHit:
    source: str
    external_id: str
    title: str
    company: str
    url: str
    salary_text: str
    published_at: str
    salary_from: int | None = None


def _request_with_retry(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Retry с экспоненциальной задержкой / exponential backoff on network errors."""
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=25, **kwargs)
            if resp.status_code in {429, 500, 502, 503, 504}:
                logger.warning("HH HTTP %s attempt %s", resp.status_code, attempt)
                time.sleep(delay)
                delay *= 2
                continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("HH network error attempt %s: %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
    if last_exc:
        raise last_exc
    raise RuntimeError("HH request failed")


def resolve_area_id(city: str, user_agent: str) -> int | None:
    headers = {"User-Agent": user_agent}
    resp = _request_with_retry("GET", HH_AREAS, params={"text": city}, headers=headers)
    if resp.status_code != 200:
        logger.warning("HH areas status %s", resp.status_code)
        return None
    items = resp.json().get("items") or []
    if not items:
        return None
    return int(items[0]["id"])


def _format_salary(salary: dict | None) -> tuple[str, int | None]:
    if not salary:
        return "не указана", None
    cur = salary.get("currency") or ""
    lo = salary.get("from")
    hi = salary.get("to")
    parts = []
    if lo is not None:
        parts.append(f"от {lo}")
    if hi is not None:
        parts.append(f"до {hi}")
    text = " ".join(parts) if parts else "не указана"
    if cur:
        text = f"{text} {cur}".strip()
    salary_from = int(lo) if lo is not None else (int(hi) if hi is not None else None)
    return text, salary_from


def search_vacancies(
    *,
    keywords: str,
    city: str,
    min_salary: int,
    user_agent: str,
    per_page: int = 20,
) -> list[VacancyHit]:
    headers = {"User-Agent": user_agent}
    params: dict[str, Any] = {
        "text": keywords,
        "per_page": per_page,
        "order_by": "publication_time",
    }
    if min_salary > 0:
        params["salary"] = min_salary
        params["only_with_salary"] = True

    area_id = resolve_area_id(city, user_agent) if city.strip() else None
    if area_id is not None:
        params["area"] = area_id
    else:
        # fallback: город в тексте запроса / city into query text
        params["text"] = f"{keywords} {city}".strip()

    resp = _request_with_retry("GET", HH_API, params=params, headers=headers)
    if resp.status_code != 200:
        logger.error("HH vacancies status %s: %s", resp.status_code, resp.text[:200])
        return []

    hits: list[VacancyHit] = []
    for item in resp.json().get("items") or []:
        salary_text, salary_from = _format_salary(item.get("salary"))
        if min_salary > 0 and salary_from is not None and salary_from < min_salary:
            continue
        employer = (item.get("employer") or {}).get("name") or ""
        hits.append(
            VacancyHit(
                source="hh",
                external_id=str(item["id"]),
                title=item.get("name") or "Без названия",
                company=employer,
                url=item.get("alternate_url") or f"https://hh.ru/vacancy/{item['id']}",
                salary_text=salary_text,
                published_at=item.get("published_at") or "",
                salary_from=salary_from,
            )
        )
    return hits
