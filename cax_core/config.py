"""Runtime API configuration shared by the parsers and web prototype."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 120
DEFAULT_DEV_CODE = "123456"


@dataclass
class VisualSettings:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: Optional[str] = None
    timeout: int = DEFAULT_TIMEOUT
    provider: str = "deepseek"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def config_paths() -> list[Path]:
    root = project_root()
    return [root / "config.local.json", root / "config.json"]


def read_config() -> dict[str, Any]:
    for path in config_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def write_local_config(updates: dict[str, Any]) -> dict[str, Any]:
    path = project_root() / "config.local.json"
    current = read_config()
    current.update({k: v for k, v in updates.items() if v is not None})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return current


def resolve_visual_settings(
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
    api_key: Optional[str] = None,
) -> VisualSettings:
    config = read_config()
    return VisualSettings(
        base_url=(base_url or os.environ.get("CAX_VLM_ENDPOINT")
                  or config.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        model=(model or os.environ.get("CAX_VLM_MODEL")
               or config.get("model") or DEFAULT_MODEL),
        api_key=(api_key or os.environ.get("CAX_VLM_API_KEY")
                 or os.environ.get("DEEPSEEK_API_KEY")
                 or config.get("api_key")),
        timeout=int(timeout or os.environ.get("CAX_VLM_TIMEOUT")
                    or config.get("timeout") or DEFAULT_TIMEOUT),
        provider=str(config.get("provider") or "deepseek"),
    )


def is_developer_code(code: Optional[str]) -> bool:
    expected = os.environ.get("CAX_DEV_CODE", DEFAULT_DEV_CODE)
    return bool(code) and code == expected


def mask_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
