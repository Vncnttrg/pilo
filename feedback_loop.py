"""
Feedback Loop
=============
Shows top 20 listings one by one. Type y/n for each.
Adjusts the style vector using likes (positive) and dislikes (negative),
re-scores all listings, saves updated style_results.json.

Usage:
    python3 feedback_loop.py
"""

import json
import warnings
import logging
from pathlib import Path

import torch
import open_clip
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="open_clip")

console = Console()

STYLE_RESULTS_FILE = Path("style_results.json")
EMBEDDINGS_FILE    = Path("embeddings.json")
LISTINGS_FILE      = Path("listings.json")
MODEL_NAME         = "ViT-B-32"
PRETRAINED         = "openai"
DEVICE             = "cpu"

# How strongly dislikes push the vector away relative to likes.
# 1.0 = symmetric. Lower = softer negative signal.
DISLIKE_WEIGHT = 0.5


# ─── Model ───────────────────────────────────────────────────────────────────

def load_model():
    console.print("[dim]Loading CLIP model…[/dim]")
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, device=DEVICE
    )
    model.eval()
    return model, preprocess


# ─── Review loop ─────────────────────────────────────────────────────────────

def review_listings(top: list[dict]) -> tuple[list[dict], list[dict]]:
    """Show each listing, collect y/n. Returns (liked, disliked)."""
    liked, disliked = [], []
    total = len(top)

    console.print(f"\n[bold]Reviewing {total} listings — [green]y[/green] to like, [red]n[/red] to dislike, [dim]s[/dim] to skip[/bold]\n")

    for i, item in enumerate(top, 1):
        price_str = f"{item['price']:.2f} {item.get('currency', 'EUR')}"
        brand     = item.get("brand", "")
        title     = item.get("title", "")
        url       = item.get("url", "")
        image_url = item.get("image_url", "")

        body = Text()
        body.append(f"{title}\n", style="bold white")
        body.append(f"{brand}  ·  {price_str}\n", style="green")
        body.append(f"{url}\n", style="dim cyan")
        body.append(f"🖼  {image_url}", style="dim")

        console.print(Panel(body, title=f"[dim]{i}/{total}[/dim]", border_style="dim"))

        while True:
            raw = console.input("[green]y[/green]/[red]n[/red]/[dim]s[/dim]kip → ").strip().lower()
            if raw in ("y", "n", "s"):
                break
            console.print("[dim]  Please type y, n, or s[/dim]")

        if raw == "y":
            liked.append(item)
            console.print("[green]  ✓ liked[/green]\n")
        elif raw == "n":
            disliked.append(item)
            console.print("[red]  ✗ disliked[/red]\n")
        else:
            console.print("[dim]  skipped[/dim]\n")

    return liked, disliked


# ─── Vector adjustment ───────────────────────────────────────────────────────

def adjust_style_vector(
    current_vec: torch.Tensor,
    liked: list[dict],
    disliked: list[dict],
    emb_index: dict[int, list[float]],
) -> torch.Tensor:
    """
    Adds liked embeddings and subtracts disliked ones from the style vector,
    then re-normalises. If there is no signal at all, returns current_vec unchanged.
    """
    delta = torch.zeros_like(current_vec)

    for item in liked:
        emb = emb_index.get(item["id"])
        if emb is not None:
            delta += torch.tensor(emb, dtype=torch.float32)

    for item in disliked:
        emb = emb_index.get(item["id"])
        if emb is not None:
            delta -= DISLIKE_WEIGHT * torch.tensor(emb, dtype=torch.float32)

    if delta.norm() == 0:
        console.print("[yellow]No signal — style vector unchanged[/yellow]")
        return current_vec

    new_vec = current_vec + delta
    return new_vec / new_vec.norm()


# ─── Scoring (mirrors style_scorer.py) ───────────────────────────────────────

PRICE_MEDIAN = 30.0

def apply_deal_scoring(entries: list[dict]) -> list[dict]:
    max_favs = max(e["favourites"] for e in entries) or 1
    for e in entries:
        price_score = PRICE_MEDIAN / (e["price"] + PRICE_MEDIAN) if e["price"] >= 0 else 0.5
        fav_score   = e["favourites"] / max_favs
        deal_score  = 0.6 * price_score + 0.4 * fav_score
        e["price_score"] = round(price_score, 4)
        e["fav_score"]   = round(fav_score,   4)
        e["deal_score"]  = round(deal_score,  4)
        e["final_score"] = round(0.7 * e["style_score"] + 0.3 * deal_score, 4)
    return sorted(entries, key=lambda x: x["final_score"], reverse=True)

def rescore(style_vec: torch.Tensor, embeddings: list[dict], listings: dict) -> list[dict]:
    ids    = [e["id"] for e in embeddings]
    matrix = torch.tensor([e["embedding"] for e in embeddings], dtype=torch.float32)
    scores = (matrix @ style_vec.squeeze()).tolist()

    merged = []
    for listing_id, score in zip(ids, scores):
        listing = listings.get(listing_id, {})
        if not listing:
            continue
        merged.append({
            "id":          listing_id,
            "style_score": round(score, 4),
            "title":       listing.get("title", ""),
            "price":       listing.get("price", 0.0),
            "currency":    listing.get("currency", "EUR"),
            "brand":       listing.get("brand", ""),
            "favourites":  listing.get("favourites", 0),
            "image_url":   listing.get("image_url", ""),
            "url":         listing.get("url", ""),
        })

    return sorted(merged, key=lambda x: x["style_score"], reverse=True)


# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    console.rule("[bold]🔁 Pilo Feedback Loop[/bold]")

    # Load data
    style_data   = json.loads(STYLE_RESULTS_FILE.read_text(encoding="utf-8"))
    emb_data     = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    listing_data = json.loads(LISTINGS_FILE.read_text(encoding="utf-8"))

    top          = style_data[:20]
    emb_index    = {e["id"]: e["embedding"] for e in emb_data["embeddings"]}
    listings     = {l["id"]: l for l in listing_data["listings"]}
    all_embs     = emb_data["embeddings"]

    console.print(f"Loaded [cyan]{len(emb_index)}[/cyan] embeddings, [cyan]{len(listings)}[/cyan] listings")

    # Review
    liked, disliked = review_listings(top)
    console.print(f"\n[bold]Summary:[/bold] [green]{len(liked)} liked[/green]  [red]{len(disliked)} disliked[/red]  [dim]{20 - len(liked) - len(disliked)} skipped[/dim]")

    if not liked and not disliked:
        console.print("[yellow]No feedback given — nothing to update.[/yellow]")
        return

    # Derive current style vector from top-1 embedding as a starting point,
    # then adjust with feedback signal
    top1_emb = emb_index.get(top[0]["id"])
    if top1_emb is None:
        console.print("[red]✗ Cannot find embedding for top listing — aborting[/red]")
        return

    # Reconstruct the approximate current style vector by averaging the top-5
    # embeddings (reasonable proxy when we don't store the inspo vector)
    top5_ids  = [item["id"] for item in top[:5] if item["id"] in emb_index]
    top5_vecs = torch.tensor([emb_index[i] for i in top5_ids], dtype=torch.float32)
    mean_vec  = top5_vecs.mean(dim=0)
    current_vec = (mean_vec / mean_vec.norm()).unsqueeze(0)

    # Adjust
    _, preprocess = load_model()  # needed to satisfy open_clip import; no inference here
    new_vec = adjust_style_vector(current_vec, liked, disliked, emb_index)

    # Re-score all listings
    console.print("\n[bold]Re-scoring all listings with updated style vector…[/bold]")
    ranked = rescore(new_vec, all_embs, listings)

    # Save top 20
    STYLE_RESULTS_FILE.write_text(
        json.dumps(ranked[:20], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    console.print(f"[green]✅ style_results.json updated — new top listing:[/green]")
    top1 = ranked[0]
    console.print(f"  [bold]{top1['title']}[/bold]  {top1['price']:.2f} {top1['currency']}  score {top1['final_score']:.4f}")


if __name__ == "__main__":
    run()
