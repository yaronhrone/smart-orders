"""
מאחזר ומפרסר את טבלת מחירון הירקות מאתר מועצת הצמחים.
URL: https://plants.moonsite.co.il/
הטבלה: Telerik RadGrid עם class="rgMasterTable", id="ctl02_RadGrid1_ctl00"
שורות נתונים: id בפורמט ctl02_RadGrid1_ctl00__<N>
"""
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MOONSITE_URL = "https://plants.moonsite.co.il/"
TABLE_ID = "ctl02_RadGrid1_ctl00"
ROW_ID_PREFIX = "ctl02_RadGrid1_ctl00__"


def _parse_price(value: str) -> Optional[Decimal]:
    if not value:
        return None
    cleaned = value.strip().replace(",", "").replace("₪", "").replace("\xa0", "").replace(" ", "")
    if not cleaned or cleaned in ("-", "--"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_date(value: str) -> Optional[date]:

    if not value:
        return None
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    logger.warning("לא ניתן לפרסר תאריך: %s", value)
    return None


def fetch_vegetable_prices(url: str = MOONSITE_URL) -> list[dict]:

    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
            },
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("שגיאת HTTP בעת גישה ל-%s: %s", url, exc)
        raise

    soup = BeautifulSoup(response.content, "lxml")

    table = soup.find("table", id=TABLE_ID) or soup.find("table", class_="rgMasterTable")
    if table is None:
        logger.warning("לא נמצאה טבלת מחירים בעמוד: %s", url)
        return []

    data_rows = table.find_all("tr", id=re.compile(r"^" + re.escape(ROW_ID_PREFIX)))
    if not data_rows:
        tbody = table.find("tbody")
        data_rows = tbody.find_all("tr") if tbody else []

    logger.debug("נמצאו %d שורות נתונים", len(data_rows))

    results = []
    for row in data_rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        name_cell = row.find("td", class_="productName") or cells[1]
        name = name_cell.get_text(strip=True)
        if not name:
            continue

        date_str = cells[0].get_text(strip=True) if cells else ""
        grade_a_str = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        premium_str = cells[3].get_text(strip=True) if len(cells) > 3 else ""

        results.append({
            "name": name,
            "market_date": _parse_date(date_str),
            "price_grade_a": _parse_price(grade_a_str),
            "price_premium": _parse_price(premium_str),
        })
    print(results)

    logger.info("נאחזו %d שורות מחירים מ-%s", len(results), url)
    return results
