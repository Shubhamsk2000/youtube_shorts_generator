from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from common.paths import setup_env

setup_env()
app = typer.Typer(add_completion=False, help="Studio: shorts + anime edits in one web app.")
console = Console()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000,
          shorts_config: Path = typer.Option(None, help="default: configs/shorts.yaml"),
          anime_config: Path = typer.Option(None, help="default: configs/anime.yaml")) -> None:
    """Start the Studio web UI + API (http://127.0.0.1:8000)."""
    import uvicorn

    from .server import create_app

    console.print(f"[bold]Studio[/] → http://{host}:{port}")
    uvicorn.run(create_app(shorts_config, anime_config), host=host, port=port, log_level="warning")


@app.callback()
def main() -> None:
    """Studio: shorts + anime edits in one web app."""


if __name__ == "__main__":
    app()
