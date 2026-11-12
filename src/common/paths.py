"""Project-wide locations. Every download, model and cache stays inside the project folder."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / ".cache"
MODELS_DIR = CACHE_DIR / "models"
WEB_DIR = PROJECT_ROOT / "web"


def setup_env() -> None:
    """Load .env and point model/tool caches (Whisper via Hugging Face, yt-dlp) at .cache/."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_DIR))
    os.environ.setdefault("HF_HOME", str(CACHE_DIR / "huggingface"))


def resolve(p: Path) -> Path:
    """Relative paths in config files are relative to the project root."""
    return p if p.is_absolute() else PROJECT_ROOT / p
