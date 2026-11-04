from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from parsers.hh import VacancyHit

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs/search/"
MAX_RETRIES = 4


def _linkedin_get(url: str, user_agent: str) -> requests.Response | None:
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    }
    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # пауза 3–5 сек между запросами / polite delay
            time.sleep(random.uniform(3.0, 5.0))
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code in {401, 403, 999}:
                logger.warning(
                    "LinkedIn blocked (HTTP %s) — skip this cycle",
                    resp.status_code,
                )
                return None
            if resp.status_code in {429, 500, 502, 503, 504}:
                logger.warning("LinkedIn HTTP %s attempt %s", resp.status_code, attempt)
                time.sleep(delay)
                delay *= 2
                continue
            return resp
        except requests.RequestException as exc:
            logger.warning("LinkedIn network error attempt %s: %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
    logger.error("LinkedIn request failed after retries")
    return None


def _parse_salary_guess(text: str) -> int | None:
    digits = re.findall(r"\d[\d\s]{2,}", text.replace("\xa0", " "))
    if not digits:
        return None
    try:
        return int(re.sub(r"\s+", "", digits[0]))
    except ValueError:
        return None


def search_vacancies(
    *,
    keywords: str,
    city: str,
    min_salary: int,
    user_agent: str,
) -> list[VacancyHit]:
    query = quote_plus(keywords)
    location = quote_plus(city)
    url = f"{SEARCH_URL}?keywords={query}&location={location}&f_TPR=r86400"
    resp = _linkedin_get(url, user_agent)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select("div.base-card, li.jobs-search-results__list-item, div.job-search-card")
    if not cards:
        # иногда другая вёрстка / alternate markup
        cards = soup.select("a.base-card__full-link")
        logger.info("LinkedIn: found %s link cards", len(cards))

    hits: list[VacancyHit] = []
    seen_ids: set[str] = set()

    for card in cards:
        link = card if card.name == "a" else card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        if link is None:
            continue
        href = link.get("href") or ""
        href = href.split("?")[0]
        if "/jobs/view/" not in href:
            continue
        full_url = urljoin("https://www.linkedin.com", href)

        match = re.search(r"/jobs/view/.*?(\d+)", full_url)
        external_id = match.group(1) if match else full_url
        if external_id in seen_ids:
            continue
        seen_ids.add(external_id)

        title_el = card.select_one("h3, .base-search-card__title, span.sr-only")
        company_el = card.select_one("h4, .base-search-card__subtitle")
        title = (title_el.get_text(strip=True) if title_el else link.get_text(strip=True)) or "Job"
        company = company_el.get_text(strip=True) if company_el else ""

        salary_text = "не указана"
        salary_el = card.select_one(".job-search-card__salary-info, .salary")
        if salary_el:
            salary_text = salary_el.get_text(" ", strip=True)
            guessed = _parse_salary_guess(salary_text)
            if min_salary > 0 and guessed is not None and guessed < min_salary:
                continue

        time_el = card.select_one("time")
        published_at = ""
        if time_el is not None:
            published_at = time_el.get("datetime") or time_el.get_text(strip=True)

        hits.append(
            VacancyHit(
                source="linkedin",
                external_id=str(external_id),
                title=title,
                company=company,
                url=full_url,
                salary_text=salary_text,
                published_at=published_at,
            )
        )

    logger.info("LinkedIn parsed %s vacancies", len(hits))
    return hits
