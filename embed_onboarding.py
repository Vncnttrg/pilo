"""
Embed Onboarding Images
=======================
One-time script. Walks pilo-app/public/onboarding/, runs CLIP ViT-B/32 on
each image, saves results to onboarding_embeddings.json.

Run from the repo root:
    python3 embed_onboarding.py

Dependencies (same as clip_scorer.py):
    pip install open_clip_torch torch torchvision Pillow
"""

import json
import logging
import warnings
from pathlib import Path

import open_clip
import torch

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="open_clip")
from PIL import Image

MODEL_NAME = "ViT-B-32"
PRETRAINED = "openai"
DEVICE = "cpu"
ONBOARDING_DIR = Path(__file__).parent / "pilo-app" / "public" / "onboarding"
OUTPUT_FILE = Path(__file__).parent / "onboarding_embeddings.json"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def embed_image(model, preprocess, path: Path) -> list[float]:
    img = Image.open(path).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        emb = model.encode_image(tensor)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.squeeze().cpu().tolist()


def run() -> None:
    print(f"Loading {MODEL_NAME} ({PRETRAINED})...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, device=DEVICE
    )
    model.eval()
    print("Model ready.\n")

    image_paths = sorted(
        p for p in ONBOARDING_DIR.rglob("*") if p.suffix.lower() in EXTS
    )
    print(f"Found {len(image_paths)} images in {ONBOARDING_DIR}\n")

    embeddings: dict[str, list[float]] = {}
    for path in image_paths:
        key = path.relative_to(ONBOARDING_DIR).as_posix()
        print(f"  embedding: {key}")
        try:
            embeddings[key] = embed_image(model, preprocess, path)
        except Exception as e:
            print(f"    ERROR: {e}")

    output = {
        "meta": {
            "model": MODEL_NAME,
            "pretrained": PRETRAINED,
            "embedding_dim": 512,
            "total": len(embeddings),
        },
        "embeddings": embeddings,
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n{len(embeddings)} embeddings saved to {OUTPUT_FILE.name}")


if __name__ == "__main__":
    run()
