"""
Style Scorer
============
Takes 3-5 local inspo images, builds a style vector via CLIP ViT-B/32,
then ranks all listings in embeddings.json by cosine similarity.

Usage:
    python3 style_scorer.py
    (place 3–5 inspo images in ./inspo/)

Dependencies:
    pip install open_clip_torch torch torchvision Pillow rich
"""

import io
import json
import sys
import warnings
import logging
from pathlib import Path

import torch
import open_clip
from PIL import Image
from rich.console import Console
from rich.table import Table

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="open_clip")

console = Console()

EMBEDDINGS_FILE = "embeddings.json"
LISTINGS_FILE   = "listings.json"
MODEL_NAME      = "ViT-B-32"
PRETRAINED      = "openai"
DEVICE          = "cpu"
TOP_N           = 20

# ─── Model ───────────────────────────────────────────────────────────────────

def load_model():
    console.print(f"[dim]Loading {MODEL_NAME} ({PRETRAINED})…[/dim]")
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, device=DEVICE
    )
    model.eval()
    return model, preprocess

# ─── Style vector ────────────────────────────────────────────────────────────

def embed_image(model, preprocess, path: Path) -> torch.Tensor:
    img = Image.open(path).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        vec = model.encode_image(tensor)
    return vec / vec.norm(dim=-1, keepdim=True)

def build_style_vector(model, preprocess, image_paths: list[Path]) -> torch.Tensor:
    console.print(f"\nEmbedding [cyan]{len(image_paths)}[/cyan] inspo image(s)…")
    vecs = []
    for p in image_paths:
        v = embed_image(model, preprocess, p)
        console.print(f"  [green]✓[/green] {p.name}")
        vecs.append(v)

    stacked = torch.cat(vecs, dim=0)          # (N, 512)
    mean    = stacked.mean(dim=0, keepdim=True)
    return mean / mean.norm(dim=-1, keepdim=True)  # L2-normalise the average

# ─── Scoring ─────────────────────────────────────────────────────────────────

PRICE_MEDIAN = 30.0  # €

def score_listings(style_vec: torch.Tensor, embeddings: list[dict]) -> list[dict]:
    ids    = [e["id"]        for e in embeddings]
    matrix = torch.tensor([e["embedding"] for e in embeddings], dtype=torch.float32)
    # style_vec is (1, 512), matrix is (N, 512); dot product = cosine sim (both L2-normed)
    scores = (matrix @ style_vec.squeeze()).tolist()
    return sorted(
        [{"id": i, "score": s} for i, s in zip(ids, scores)],
        key=lambda x: x["score"],
        reverse=True,
    )

def apply_deal_scoring(entries: list[dict]) -> list[dict]:
    """
    Adds deal_score and final_score to each entry, then re-ranks.

    price_score  — smooth decay: median / (price + median)
                   → free item ≈ 1.0, at median = 0.5, at 390€ ≈ 0.07
    fav_score    — linear: favourites / max_favourites across all entries
    deal_score   — 0.6 * price_score + 0.4 * fav_score
    final_score  — 0.7 * style_score + 0.3 * deal_score
    """
    max_favs = max(e["favourites"] for e in entries) or 1  # avoid div/0

    for e in entries:
        price_score = PRICE_MEDIAN / (e["price"] + PRICE_MEDIAN) if e["price"] >= 0 else 0.5
        fav_score   = e["favourites"] / max_favs
        deal_score  = 0.6 * price_score + 0.4 * fav_score
        final_score = 0.7 * e["style_score"] + 0.3 * deal_score

        e["price_score"] = round(price_score, 4)
        e["fav_score"]   = round(fav_score,   4)
        e["deal_score"]  = round(deal_score,  4)
        e["final_score"] = round(final_score, 4)

    return sorted(entries, key=lambda x: x["final_score"], reverse=True)

# ─── Main ────────────────────────────────────────────────────────────────────

def run(image_paths: list[Path]):
    console.rule("[bold]🎨 Style Scorer – CLIP ViT-B/32[/bold]")

    # Validate inputs
    for p in image_paths:
        if not p.exists():
            console.print(f"[red]✗ File not found: {p}[/red]")
            sys.exit(1)

    # Load data
    emb_data     = json.loads(Path(EMBEDDINGS_FILE).read_text(encoding="utf-8"))
    listing_data = json.loads(Path(LISTINGS_FILE).read_text(encoding="utf-8"))

    embeddings = emb_data["embeddings"]
    listings   = {l["id"]: l for l in listing_data["listings"]}
    console.print(f"Loaded [cyan]{len(embeddings)}[/cyan] embeddings, [cyan]{len(listings)}[/cyan] listings")

    # Build style vector and score
    model, preprocess = load_model()
    style_vec = build_style_vector(model, preprocess, image_paths)
    ranked    = score_listings(style_vec, embeddings)

    # Merge ALL ranked entries with listing metadata (needed for deal scoring)
    merged = []
    for entry in ranked:
        listing = listings.get(entry["id"], {})
        image_url  = listing.get("image_url", "")
        image_urls = listing.get("image_urls") or ([image_url] if image_url else [])
        merged.append({
            "id":          entry["id"],
            "style_score": round(entry["score"], 4),
            "title":       listing.get("title", ""),
            "price":       listing.get("price", 0.0),
            "currency":    listing.get("currency", "EUR"),
            "favourites":  listing.get("favourites", 0),
            "image_url":   image_url,
            "image_urls":  image_urls,
            "url":         listing.get("url", ""),
        })

    # Apply deal scoring across all entries, then take top N
    ranked_final = apply_deal_scoring(merged)
    top = ranked_final[:TOP_N]

    # Print table
    console.print()
    table = Table(title=f"Top {TOP_N} listings — style 70% + deal 30%", show_lines=True)
    table.add_column("Rank",        style="dim",    justify="right", width=4)
    table.add_column("Final",                       justify="right", width=6)
    table.add_column("Style",       style="dim",    justify="right", width=6)
    table.add_column("Deal",        style="dim",    justify="right", width=6)
    table.add_column("Title",                       max_width=28)
    table.add_column("Price",       style="green",  justify="right", width=8)
    table.add_column("Favs",        style="dim",    justify="right", width=4)
    table.add_column("URL",         style="dim",    max_width=36)

    for i, item in enumerate(top, 1):
        table.add_row(
            str(i),
            f"{item['final_score']:.4f}",
            f"{item['style_score']:.4f}",
            f"{item['deal_score']:.4f}",
            item["title"],
            f"{item['price']:.2f} {item['currency']}",
            str(item["favourites"]),
            item["url"],
        )

    console.print(table)

    # Save results
    output_path = Path("style_results.json")
    output_path.write_text(json.dumps(top, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\n[green]✅ Results saved to {output_path}[/green]")


if __name__ == "__main__":
    inspo_dir = Path("inspo")
    if not inspo_dir.is_dir():
        console.print("[red]✗ ./inspo/ folder not found[/red]")
        sys.exit(1)

    paths = sorted(
        p for p in inspo_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )

    if not paths:
        console.print("[red]✗ No images found in ./inspo/ — add .jpg/.png/.webp files[/red]")
        sys.exit(1)

    if not 1 <= len(paths) <= 10:
        console.print(f"[yellow]⚠  Found {len(paths)} images — 3–5 recommended for a balanced style vector[/yellow]")

    run(paths)
