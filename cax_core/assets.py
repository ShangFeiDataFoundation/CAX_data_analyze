"""Helpers for deterministic asset references and source descriptions."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from typing import Any, Optional

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow is optional
    Image = None  # type: ignore[assignment]


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: str, base_dir: Optional[str]) -> str:
    if not base_dir:
        return path.replace("\\", "/")
    try:
        return os.path.relpath(path, base_dir).replace("\\", "/")
    except ValueError:
        return os.path.abspath(path).replace("\\", "/")


def render_asset(view: str, path: Optional[str], base_dir: Optional[str],
                 error: Optional[str] = None) -> dict[str, Any]:
    if not path:
        return {"view": view, "path": None, "error": error}
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    item: dict[str, Any] = {
        "view": view,
        "path": relative_path(path, base_dir),
        "mime_type": mime,
        "bytes": os.path.getsize(path) if os.path.isfile(path) else None,
        "sha256": sha256_file(path) if os.path.isfile(path) else None,
        "width": None,
        "height": None,
        "error": error,
    }
    if Image is not None and mime.startswith("image/") and os.path.isfile(path):
        try:
            with Image.open(path) as image:
                item["width"], item["height"] = image.size
        except Exception:
            pass
    return item


def render_assets(views: list[dict[str, Any]], base_dir: Optional[str]) -> list[dict[str, Any]]:
    return [
        render_asset(
            str(view.get("view", "unknown")),
            view.get("path") or view.get("file"),
            base_dir,
            view.get("error"),
        )
        for view in views
    ]
