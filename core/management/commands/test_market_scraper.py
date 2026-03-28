"""
management command לבדיקת הסקריפר של מועצת הצמחים.

שימוש:
  python manage.py test_market_scraper
  python manage.py test_market_scraper --url https://...
  python manage.py test_market_scraper --raw       # מדפיס את ה-HTML הגולמי
"""
import requests
from django.core.management.base import BaseCommand
from django.conf import settings
from bs4 import BeautifulSoup


class Command(BaseCommand):
    help = "בודק את קריאת ה-HTML ופרסור הטבלה ממועצת הצמחים"

    def add_arguments(self, parser):
        parser.add_argument("--url", type=str, default=None, help="URL לבדיקה (ברירת מחדל: PLANT_COUNCIL_PRICES_URL)")
        parser.add_argument("--raw", action="store_true", help="הדפס את ה-HTML הגולמי")
        parser.add_argument("--tables", action="store_true", help="הדפס את כל הטבלאות שנמצאו בעמוד")

    def handle(self, *args, **options):
        url = options["url"] or getattr(settings, "PLANT_COUNCIL_PRICES_URL", "")

        if not url:
            self.stderr.write(self.style.ERROR("לא הוגדר URL. השתמש ב: --url https://... או הגדר PLANT_COUNCIL_PRICES_URL"))
            return

        self.stdout.write(f"מאחזר: {url}")

        # --- שלב 1: קריאת HTTP ---
        try:
            response = requests.get(
                url,
                timeout=20,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; SmartOrder/1.0)",
                    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
                },
            )
            self.stdout.write(f"סטטוס HTTP: {response.status_code}")
            self.stdout.write(f"Content-Type: {response.headers.get('Content-Type', 'לא ידוע')}")
            self.stdout.write(f"גודל תגובה: {len(response.content)} bytes")
        except requests.RequestException as e:
            self.stderr.write(self.style.ERROR(f"שגיאת HTTP: {e}"))
            return

        if response.status_code != 200:
            self.stderr.write(self.style.ERROR("הבקשה נכשלה, בדוק את ה-URL"))
            return

        # --- שלב 2: HTML גולמי ---
        if options["raw"]:
            self.stdout.write("\n--- HTML גולמי (500 תווים ראשונים) ---")
            self.stdout.write(response.text[:500])
            return

        soup = BeautifulSoup(response.content, "lxml")

        # --- שלב 3: סקירת כל הטבלאות ---
        all_tables = soup.find_all("table")
        self.stdout.write(f"\nנמצאו {len(all_tables)} טבלאות בעמוד")

        if options["tables"] or not all_tables:
            for i, table in enumerate(all_tables):
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
                if not headers:
                    first_row = table.find("tr")
                    if first_row:
                        headers = [td.get_text(strip=True) for td in first_row.find_all(["td", "th"])]
                row_count = len(table.find_all("tr"))
                self.stdout.write(f"  טבלה #{i+1}: {row_count} שורות | כותרות: {headers}")

        # --- שלב 4: פרסור מלא ---
        from apps.catalog.market_scraper import fetch_vegetable_prices
        try:
            rows = fetch_vegetable_prices(url)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"שגיאה בפרסור: {e}"))
            return

        if not rows:
            self.stderr.write(self.style.WARNING("לא נמצאו שורות — הטבלה לא זוהתה או ריקה"))
            self.stdout.write("\nטיפ: הרץ עם --tables לראות את כל הטבלאות, או --raw לראות את ה-HTML")
            return

        self.stdout.write(self.style.SUCCESS(f"\nנמצאו {len(rows)} מוצרים:"))
        self.stdout.write(f"{'שם מוצר':<25} {'תאריך':<12} {'סוג א':<10} {'מובחר':<10}")
        self.stdout.write("-" * 60)
        for row in rows:
            name = row["name"]
            date = str(row["market_date"]) if row["market_date"] else "—"
            grade_a = str(row["price_grade_a"]) if row["price_grade_a"] is not None else "—"
            premium = str(row["price_premium"]) if row["price_premium"] is not None else "—"
            self.stdout.write(f"{name:<25} {date:<12} {grade_a:<10} {premium:<10}")
