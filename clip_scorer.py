"""
Clip Scorer
===========
Loads listings.json, downloads each listing's image, runs CLIP ViT-B/32
to get an image embedding, saves results to embeddings.json.

Dependencies:
    pip install open_clip_torch torch torchvision Pillow httpx rich
"""

import io
import json
import time
import random
import warnings
import logging
from pathlib import Path

import httpx
import torch
import open_clip

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="open_clip")
from PIL import Image
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

# ─── Config ──────────────────────────────────────────────────────────────────

LISTINGS_FILE   = "listings.json"
OUTPUT_FILE     = "embeddings.json"
MODEL_NAME      = "ViT-B-32"
PRETRAINED      = "openai"          # openai weights match original CLIP paper
DEVICE          = "cpu"             # change to "cuda" or "mps" if available
DELAY_MIN       = 0.3               # seconds between image downloads
DELAY_MAX       = 0.9

# ─── Model ───────────────────────────────────────────────────────────────────

def load_model():
    console.print(f"[dim]Loading {MODEL_NAME} ({PRETRAINED})…[/dim]")
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, device=DEVICE
    )
    model.eval()
    console.print(f"[green]✓ Model ready on {DEVICE}[/green]")
    return model, preprocess

# ─── Image download ──────────────────────────────────────────────────────────

def make_http_client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.vinted.de/",
        },
        timeout=20,
        follow_redirects=True,
    )

def download_image(client: httpx.Client, url: str) -> Image.Image | None:
    try:
        r = client.get(url)
        if r.status_code != 200:
            return None
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        console.print(f"[dim]Download failed ({e})[/dim]")
        return None

# ─── Embedding ───────────────────────────────────────────────────────────────

def embed_image(model, preprocess, img: Image.Image) -> list[float]:
    tensor = preprocess(img).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        embedding = model.encode_image(tensor)
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)  # L2-normalise
    return embedding.squeeze().cpu().tolist()

# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    console.rule("[bold]🔍 CLIP Embedder – ViT-B/32[/bold]")

    listings_path = Path(LISTINGS_FILE)
    if not listings_path.exists():
        console.print(f"[red]✗ {LISTINGS_FILE} not found[/red]")
        return

    data = json.loads(listings_path.read_text(encoding="utf-8"))
    listings = data["listings"]
    console.print(f"Loaded [cyan]{len(listings)}[/cyan] listings from {LISTINGS_FILE}\n")

    model, preprocess = load_model()
    http = make_http_client()

    results = []
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Embedding…", total=len(listings))

        for listing in listings:
            listing_id  = listing["id"]
            image_url   = listing.get("image_url", "")
            progress.update(task, description=f"ID {listing_id}")

            if not image_url:
                console.print(f"[dim]No image URL for {listing_id} – skipping[/dim]")
                skipped += 1
                progress.advance(task)
                continue

            img = download_image(http, image_url)
            if img is None:
                console.print(f"[yellow]⚠  Could not download image for {listing_id}[/yellow]")
                skipped += 1
                progress.advance(task)
                continue

            embedding = embed_image(model, preprocess, img)

            results.append({
                "id":        listing_id,
                "image_url": image_url,
                "embedding": embedding,
            })

            progress.advance(task)
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # ─── Save ────────────────────────────────────────────────────────────────

    output = {
        "meta": {
            "model":         MODEL_NAME,
            "pretrained":    PRETRAINED,
            "embedding_dim": len(results[0]["embedding"]) if results else 0,
            "total":         len(results),
            "skipped":       skipped,
        },
        "embeddings": results,
    }
    Path(OUTPUT_FILE).write_text(json.dumps(output, indent=2), encoding="utf-8")

    console.print(f"\n[green]✅ {len(results)} embeddings saved to {OUTPUT_FILE}[/green]")
    console.print(f"   Skipped: {skipped}  |  Embedding dim: {output['meta']['embedding_dim']}")


if __name__ == "__main__":
    run()
