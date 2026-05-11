"""
Daily Update
============
1. Scrapes newest Vinted listings, stops when hitting already-known IDs
2. Appends only new listings to listings.json
3. Runs CLIP on new images only, appends to embeddings.json
4. Re-runs style scoring against inspo/ and updates style_results.json

Run once a day:
    cd ~/pilo && python3 daily_update.py
"""

import io
import json
import time
import random
import warnings
import logging
from pathlib import Path

import torch
from PIL import Image
from rich.console import Console
from rich.rule import Rule

# ── shared modules ────────────────────────────────────────────────────────────
from vinted_scraper import VintedClient, parse_listing, CONFIG as SCRAPE_CONFIG
from clip_scorer    import load_model, make_http_client, embed_image
from style_scorer   import run as style_run

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="open_clip")

console = Console()

# ── config ────────────────────────────────────────────────────────────────────

LISTINGS_FILE   = Path("listings.json")
EMBEDDINGS_FILE = Path("embeddings.json")
INSPO_DIR       = Path("inspo")

# Stop paginating after this many consecutive already-known IDs in a row.
# newest_first ordering means hitting stale IDs means we've passed the fresh window.
STALE_STREAK_LIMIT = 10

# Hard cap — no more than this many pages per run regardless
MAX_PAGES = 5

# ── helpers ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ── step 1: scrape new listings ───────────────────────────────────────────────

def scrape_new(existing_ids: set) -> list[dict]:
    console.print("\n[bold]Step 1 — Scrape new listings[/bold]")

    client = VintedClient(proxies=SCRAPE_CONFIG.get("proxies", []))
    client.bootstrap_session()
    time.sleep(random.uniform(2, 4))

    new_listings = []
    page         = 1
    stale_streak = 0

    while page <= MAX_PAGES:
        console.print(f"[dim]  page {page} | {len(new_listings)} new so far | stale streak: {stale_streak}[/dim]")
        try:
            data = client.search_items(page=page)
        except Exception as e:
            console.print(f"[red]  Request failed: {e}[/red]")
            break

        items = data.get("items", [])
        if not items:
            break

        for raw in items:
            listing = parse_listing(raw)
            if listing is None or not listing.image_url:
                continue

            if listing.id in existing_ids:
                stale_streak += 1
                if stale_streak >= STALE_STREAK_LIMIT:
                    console.print(f"[dim]  Hit {STALE_STREAK_LIMIT} consecutive known IDs — stopping[/dim]")
                    return new_listings
            else:
                stale_streak = 0
                new_listings.append(listing)

        page += 1
        delay = random.uniform(
            SCRAPE_CONFIG["delay_min"],
            SCRAPE_CONFIG["delay_max"],
        )
        time.sleep(delay)

    return new_listings

# ── step 2: embed new listings ────────────────────────────────────────────────

def embed_new(new_listings: list) -> list[dict]:
    console.print("\n[bold]Step 2 — CLIP embed new images[/bold]")

    model, preprocess = load_model()
    http    = make_http_client()
    results = []
    skipped = 0

    for listing in new_listings:
        console.print(f"[dim]  {listing.id} — {listing.title[:40]}[/dim]")
        try:
            r = http.get(listing.image_url, timeout=20)
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:
            console.print(f"[yellow]  ⚠ Image download failed ({e})[/yellow]")
            skipped += 1
            continue

        embedding = embed_image(model, preprocess, img)
        results.append({
            "id":        listing.id,
            "image_url": listing.image_url,
            "embedding": embedding,
        })
        time.sleep(random.uniform(0.3, 0.9))

    console.print(f"  Embedded: [green]{len(results)}[/green]  Skipped: [yellow]{skipped}[/yellow]")
    return results

# ── step 3: update style results ─────────────────────────────────────────────

def update_style():
    console.print("\n[bold]Step 3 — Re-score style rankings[/bold]")
    paths = sorted(
        p for p in INSPO_DIR.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not paths:
        console.print("[yellow]  No inspo images found — skipping style scoring[/yellow]")
        return
    style_run(paths)

# ── main ──────────────────────────────────────────────────────────────────────

def run():
    console.rule("[bold]🔄 Pilo Daily Update[/bold]")

    # Load existing data
    listing_data = load_json(LISTINGS_FILE)
    emb_data     = load_json(EMBEDDINGS_FILE)

    existing_ids      = {l["id"] for l in listing_data["listings"]}
    existing_emb_ids  = {e["id"] for e in emb_data["embeddings"]}

    console.print(
        f"Existing: [cyan]{len(existing_ids)}[/cyan] listings, "
        f"[cyan]{len(existing_emb_ids)}[/cyan] embeddings"
    )

    # ── 1. scrape ─────────────────────────────────────────────────────────────
    from dataclasses import asdict
    new_listings = scrape_new(existing_ids)

    if not new_listings:
        console.print("[green]✓ No new listings found — already up to date[/green]")
        update_style()
        return

    console.print(f"  [green]{len(new_listings)} new listing(s) found[/green]")

    # Append to listings.json
    listing_data["listings"].extend(asdict(l) for l in new_listings)
    listing_data["meta"]["total_scraped"] = len(listing_data["listings"])
    save_json(LISTINGS_FILE, listing_data)
    console.print(f"  listings.json → {len(listing_data['listings'])} total")

    # ── 2. embed ──────────────────────────────────────────────────────────────
    # Only embed listings that don't already have an embedding
    to_embed = [l for l in new_listings if l.id not in existing_emb_ids]
    new_embeddings = embed_new(to_embed)

    if new_embeddings:
        emb_data["embeddings"].extend(new_embeddings)
        emb_data["meta"]["total"] = len(emb_data["embeddings"])
        save_json(EMBEDDINGS_FILE, emb_data)
        console.print(f"  embeddings.json → {len(emb_data['embeddings'])} total")

    # ── 3. re-score ───────────────────────────────────────────────────────────
    update_style()

    console.print("\n[bold green]✅ Daily update complete[/bold green]")


if __name__ == "__main__":
    run()
