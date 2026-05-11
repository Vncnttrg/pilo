"""
Pilo – Vinted Unofficial API Scraper
=====================================
Scrapes 3 catalogs in parallel (Herren Oberteile, Jacken, Hosen),
merges results, deduplicates by id, saves to listings.json,
then runs clip_scorer.py on the full set.

IMPORTANT: Personal learning / MVP validation only.
           Respect Vinted ToS. Do not use at production scale.

Dependencies:
    pip install httpx tenacity rich
"""

import json
import time
import random
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.table import Table

console = Console()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

# ─── Config ──────────────────────────────────────────────────────────────────

CONFIG = {
    "search_text": "vintage streetwear",
    "size_ids":    [207],       # 207 = M (Herren)
    "country_id":  16,          # 16 = Deutschland
    "currency":    "EUR",
    "order":       "newest_first",

    "max_per_catalog": 500,     # per catalog; total before dedup = 3 × 500
    "per_page":    96,          # Vinted max per request
    "delay_min":   2.5,
    "delay_max":   5.5,
    "output_file": "listings.json",
    "proxies":     [],
}

CATALOGS = {
    "Herren Oberteile": 1206,
    "Herren Jacken":    1232,
    "Herren Hosen":     1208,
}

# ─── Data model ──────────────────────────────────────────────────────────────

@dataclass
class Listing:
    id: int
    title: str
    price: float
    currency: str
    size: str
    brand: str
    status: str
    url: str
    image_url: str
    image_urls: list
    category: str
    catalog_name: str           # which of the 3 catalogs this came from
    seller_id: int
    seller_login: str
    views: int
    favourites: int
    created_at: str
    updated_at: str
    is_vintage: bool = False
    color: str = ""
    material: str = ""


# ─── Client ──────────────────────────────────────────────────────────────────

class VintedClient:
    """
    One instance per catalog thread — httpx.Client is not safe to share
    across threads, so each scrape worker owns its own client + cookie jar.
    """

    BASE_URL = "https://www.vinted.de"
    API_URL  = "https://www.vinted.de/api/v2"

    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) "
        "Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]

    def __init__(self, proxies: list = None):
        self.proxies = proxies or []
        self._build_client()

    def _build_client(self):
        proxy = random.choice(self.proxies) if self.proxies else None
        self.client = httpx.Client(
            headers={
                "User-Agent": random.choice(self.USER_AGENTS),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                "Referer": "https://www.vinted.de/",
                "Origin": "https://www.vinted.de",
                "DNT": "1",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
            proxy=proxy,
            timeout=30,
            follow_redirects=True,
        )

    def _rotate_client(self):
        self.client.close()
        time.sleep(random.uniform(12, 22))
        self._build_client()
        self.bootstrap_session()

    def bootstrap_session(self) -> bool:
        try:
            r = self.client.get(self.BASE_URL + "/", timeout=15)
            return bool(r.cookies)
        except Exception:
            return False

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=5, max=45),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        before_sleep=before_sleep_log(log, logging.WARNING),
    )
    def search_items(self, catalog_id: int, page: int = 1) -> dict:
        params = [
            ("search_text", CONFIG["search_text"]),
            ("country_id",  CONFIG["country_id"]),
            ("currency",    CONFIG["currency"]),
            ("order",       CONFIG["order"]),
            ("page",        page),
            ("per_page",    CONFIG["per_page"]),
            ("with_photo",  "true"),
            ("catalog_ids[]", catalog_id),
        ]
        for sid in CONFIG["size_ids"]:
            params.append(("size_ids[]", sid))

        response = self.client.get(f"{self.API_URL}/catalog/items", params=params)

        if response.status_code == 429:
            time.sleep(random.uniform(90, 150))
            raise httpx.TimeoutException("rate-limited", request=response.request)

        if response.status_code == 401:
            self.bootstrap_session()
            raise httpx.TimeoutException("auth-refresh", request=response.request)

        if response.status_code == 403:
            self._rotate_client()
            raise httpx.TimeoutException("blocked", request=response.request)

        if response.status_code != 200:
            return {}

        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            console.print(f"[red]🤖 Captcha (Content-Type) on catalog {catalog_id}[/red]")
            console.print(response.text[:500])
            time.sleep(random.uniform(45, 90))
            self._rotate_client()
            raise httpx.TimeoutException("captcha-html", request=response.request)

        if not response.text.strip().startswith("{"):
            console.print(f"[red]🤖 Captcha (no JSON body) on catalog {catalog_id}[/red]")
            console.print(response.text[:500])
            time.sleep(random.uniform(45, 90))
            self._rotate_client()
            raise httpx.TimeoutException("captcha-body", request=response.request)

        return response.json()


# ─── Parser ──────────────────────────────────────────────────────────────────

def parse_listing(raw: dict, catalog_name: str = "") -> Optional[Listing]:
    try:
        photos = raw.get("photos", [])
        image_urls = []
        for photo in photos:
            url = (
                photo.get("full_size_url")
                or photo.get("high_resolution", {}).get("url")
                or photo.get("url", "")
            )
            if url:
                image_urls.append(url)

        price_field = raw.get("price", {})
        try:
            if isinstance(price_field, dict):
                price = float(price_field.get("amount", 0))
            else:
                price = float(str(raw.get("price_numeric") or price_field or 0).replace(",", "."))
        except (ValueError, TypeError):
            price = 0.0

        price_currency = price_field.get("currency_code") if isinstance(price_field, dict) else None
        catalog = raw.get("catalog") or raw.get("category") or {}
        user    = raw.get("user", {})

        return Listing(
            id           = raw.get("id", 0),
            title        = raw.get("title", ""),
            price        = price,
            currency     = price_currency or raw.get("currency", "EUR"),
            size         = raw.get("size_title") or raw.get("size", ""),
            brand        = raw.get("brand_title") or raw.get("brand", {}).get("title", ""),
            status       = raw.get("status", ""),
            url          = f"https://www.vinted.de/items/{raw.get('id')}",
            image_url    = image_urls[0] if image_urls else "",
            image_urls   = image_urls,
            category     = catalog.get("title", ""),
            catalog_name = catalog_name,
            seller_id    = user.get("id", 0),
            seller_login = user.get("login", ""),
            views        = raw.get("view_count", 0),
            favourites   = raw.get("favourite_count", 0),
            created_at   = raw.get("created_at_ts") or raw.get("created_at", ""),
            updated_at   = raw.get("updated_at_ts") or raw.get("updated_at", ""),
            is_vintage   = raw.get("is_vintage", False),
            color        = raw.get("color1_title", ""),
            material     = raw.get("composition", ""),
        )
    except Exception as e:
        console.print(f"[dim]Parse error: {e}[/dim]")
        return None


# ─── Per-catalog worker ──────────────────────────────────────────────────────

def scrape_catalog(
    name: str,
    catalog_id: int,
    progress: Progress,
    task_id,
) -> list[Listing]:
    """Runs in its own thread — owns its own VintedClient."""
    client = VintedClient(proxies=CONFIG["proxies"])
    client.bootstrap_session()
    time.sleep(random.uniform(1, 3))  # stagger thread starts

    listings: list[Listing] = []
    page = 1

    while len(listings) < CONFIG["max_per_catalog"]:
        progress.update(task_id, description=f"{name} — page {page}")
        try:
            data = client.search_items(catalog_id=catalog_id, page=page)
        except Exception as e:
            console.print(f"[red]{name}: failed on page {page}: {e}[/red]")
            break

        items = data.get("items", [])
        if not items:
            break

        for raw in items:
            if len(listings) >= CONFIG["max_per_catalog"]:
                break
            listing = parse_listing(raw, catalog_name=name)
            if listing and listing.image_url:
                listings.append(listing)
                progress.advance(task_id)

        page += 1

        delay = random.uniform(CONFIG["delay_min"], CONFIG["delay_max"])
        time.sleep(delay)

        if page % 5 == 0:
            time.sleep(random.uniform(15, 35))

    return listings


# ─── Main scraper ─────────────────────────────────────────────────────────────

def run_scraper() -> list[Listing]:
    console.rule("[bold]🛍  Pilo – Vinted Scraper (3 catalogs)[/bold]")
    console.print(
        f"Catalogs: [cyan]{', '.join(CATALOGS)}[/cyan]\n"
        f"Target: [cyan]{CONFIG['max_per_catalog']} per catalog[/cyan] "
        f"(up to {CONFIG['max_per_catalog'] * len(CATALOGS)} before dedup)\n"
    )

    all_listings: list[Listing] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        tasks = {
            name: progress.add_task(name, total=CONFIG["max_per_catalog"])
            for name in CATALOGS
        }

        with ThreadPoolExecutor(max_workers=len(CATALOGS)) as pool:
            futures = {
                pool.submit(scrape_catalog, name, cid, progress, tasks[name]): name
                for name, cid in CATALOGS.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results = future.result()
                    console.print(f"[green]✓ {name}: {len(results)} listings[/green]")
                    all_listings.extend(results)
                except Exception as e:
                    console.print(f"[red]✗ {name} failed: {e}[/red]")

    # ─── Dedup by id (keep first occurrence) ─────────────────────────────────

    seen: set[int] = set()
    unique: list[Listing] = []
    for l in all_listings:
        if l.id not in seen:
            seen.add(l.id)
            unique.append(l)

    dupes = len(all_listings) - len(unique)
    console.print(
        f"\nTotal collected: [cyan]{len(all_listings)}[/cyan] | "
        f"Duplicates removed: [yellow]{dupes}[/yellow] | "
        f"Unique: [green]{len(unique)}[/green]"
    )

    # ─── Save ─────────────────────────────────────────────────────────────────

    output_path = Path(CONFIG["output_file"])
    payload = {
        "meta": {
            "search":        CONFIG["search_text"],
            "catalogs":      CATALOGS,
            "total_scraped": len(unique),
            "duplicates_removed": dupes,
        },
        "listings": [asdict(l) for l in unique],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]✅ {len(unique)} listings saved to {output_path}[/green]\n")

    # ─── Summary ──────────────────────────────────────────────────────────────

    table = Table(title="Sample – first 10 listings", show_lines=True)
    table.add_column("ID",      style="dim")
    table.add_column("Title",   max_width=28)
    table.add_column("Catalog", max_width=16)
    table.add_column("Price",   justify="right", style="green")
    table.add_column("Brand",   max_width=14)

    for l in unique[:10]:
        table.add_row(str(l.id), l.title[:28], l.catalog_name, f"{l.price:.2f} €", l.brand[:14])

    console.print(table)

    by_catalog = {}
    for l in unique:
        by_catalog[l.catalog_name] = by_catalog.get(l.catalog_name, 0) + 1
    for name, count in by_catalog.items():
        console.print(f"  {name}: {count}")

    prices = [l.price for l in unique if l.price > 0]
    if prices:
        s = sorted(prices)
        console.print(f"\n📈 Price — median: {s[len(s)//2]:.2f} €  | min: {min(s):.2f} €  | max: {max(s):.2f} €")

    # ─── Run clip_scorer ──────────────────────────────────────────────────────

    console.print("\n[bold]Running clip_scorer.py on full listing set…[/bold]")
    result = subprocess.run(
        [sys.executable, "clip_scorer.py"],
        cwd=Path(__file__).parent,
    )
    if result.returncode != 0:
        console.print("[red]✗ clip_scorer.py failed[/red]")
    else:
        console.print("[green]✓ clip_scorer.py complete[/green]")

    return unique


if __name__ == "__main__":
    run_scraper()
