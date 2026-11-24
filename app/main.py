"""Entrypoint for Concert Multi-Cam Audio Sync."""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_repo_on_path() -> None:
    # PyInstaller bundles packages; no need to mutate sys.path.
    if getattr(sys, "frozen", False):
        return
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def main() -> int:
    _ensure_repo_on_path()
    from app.gui.window import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
