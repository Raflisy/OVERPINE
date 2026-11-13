"""Safe static-file lookup and MIME handling for the local frontend."""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


@dataclass(frozen=True, slots=True)
class StaticFile:
    """Represent a verified frontend file ready to send."""

    path: Path
    content_type: str


def resolve_static_file(frontend_dir: Path, url_path: str) -> StaticFile | None:
    """Resolve a URL beneath the frontend root without traversal or symlink escape."""
    decoded_path = unquote(url_path)
    if "\x00" in decoded_path or "\\" in decoded_path or decoded_path.startswith("//"):
        return None
    relative_url = decoded_path.lstrip("/")
    if not relative_url:
        relative_url = "index.html"
    parts = relative_url.split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        return None

    root = frontend_dir.resolve()
    try:
        candidate = root.joinpath(*parts).resolve(strict=True)
        if not candidate.is_relative_to(root):
            return None
        if candidate.is_dir():
            candidate = candidate.joinpath("index.html").resolve(strict=True)
            if not candidate.is_relative_to(root):
                return None
        if not candidate.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None

    content_type, _encoding = mimetypes.guess_type(candidate.name)
    if content_type is None:
        content_type = "application/octet-stream"
    if content_type.startswith("text/") or content_type in {
        "application/javascript",
        "application/json",
        "image/svg+xml",
    }:
        content_type += "; charset=utf-8"
    return StaticFile(candidate, content_type)


def cache_control(url_path: str) -> str:
    """Choose a safe cache lifetime for HTML and static frontend assets."""
    if url_path == "/" or url_path.casefold().endswith((".html", ".htm")):
        return "no-cache"
    return "public, max-age=3600"
