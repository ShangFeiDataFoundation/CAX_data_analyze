"""CAD visual-analysis compatibility wrapper."""

from __future__ import annotations

from typing import Any

from cax_core.vision import (
    ApiCaptioner,
    RuleBasedCaptioner,
    describe as _describe,
    make_captioner as _make_captioner,
)


def make_captioner(*, api_key: str | None = None) -> RuleBasedCaptioner | ApiCaptioner:
    return _make_captioner("cad", api_key=api_key)


def describe(content: dict[str, Any], views: list[dict[str, Any]] | None = None,
             captioner: Any = None) -> dict[str, Any]:
    return _describe("cad", content, views, captioner)


__all__ = ["ApiCaptioner", "RuleBasedCaptioner", "describe", "make_captioner"]
