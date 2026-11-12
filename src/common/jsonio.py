"""Stage files are pydantic models saved as JSON."""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def save_json(model: BaseModel, path: Path) -> None:
    """Atomic write so an interrupted run never leaves a half-written stage file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(model.model_dump_json(indent=1))
    tmp.replace(path)


def load_json(cls: type[T], path: Path) -> T:
    return cls.model_validate_json(path.read_text())
