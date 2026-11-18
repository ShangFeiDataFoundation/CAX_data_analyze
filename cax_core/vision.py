"""Rule-based and DeepSeek vision descriptions with a searchable profile."""

from __future__ import annotations

import base64
import datetime as dt
import json
import mimetypes
import os
import re
from typing import Any, Optional

import httpx

from .config import VisualSettings, resolve_visual_settings
from .perf import extract_token_usage, span


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，;；\n]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, result))


def _normalize_objects(value: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return objects
    for item in value:
        if isinstance(item, str):
            objects.append({"name_zh": item, "name_en": "", "count": 1, "confidence": None})
        elif isinstance(item, dict):
            objects.append({
                "name_zh": _text(item.get("name_zh") or item.get("name")),
                "name_en": _text(item.get("name_en")),
                "count": int(item.get("count") or 1),
                "confidence": _float(item.get("confidence")),
            })
    return objects


def normalize_profile(data: dict[str, Any]) -> dict[str, Any]:
    primary = _text(data.get("primary_class") or "unknown") or "unknown"
    display = _text(data.get("display_name_zh"))
    aliases_zh = _strings(data.get("aliases_zh"))
    aliases_en = _strings(data.get("aliases_en"))
    traits = _strings(data.get("shape_traits"))
    search_zh = _text(data.get("search_text_zh"))
    search_en = _text(data.get("search_text_en"))
    if not search_zh:
        search_zh = "，".join([display, *aliases_zh, *traits])
    if not search_en:
        search_en = ", ".join([primary, *aliases_en])
    return {
        "primary_class": primary,
        "display_name_zh": display,
        "aliases_zh": aliases_zh,
        "aliases_en": aliases_en,
        "shape_traits": traits,
        "functional_guess": _text(data.get("functional_guess")),
        "confidence": _float(data.get("confidence")),
        "evidence": _strings(data.get("evidence")),
        "search_text_zh": search_zh,
        "search_text_en": search_en,
        "objects": _normalize_objects(data.get("objects")),
    }


def rule_profile(data_type: str, content: dict[str, Any]) -> dict[str, Any]:
    if data_type == "cad":
        parts = content.get("parts") or []
        first = parts[0] if parts else {}
        topology = first.get("topology") or {}
        features = first.get("features") or []
        face_types = first.get("face_types") or []
        traits = [f"{item.get('type', 'unknown')} 面" for item in face_types[:8]]
        traits += [str(item.get("type")) for item in features[:12] if item.get("type")]
        traits = list(dict.fromkeys(traits))
        tags = [str(item.get("type")) for item in features if item.get("type")]
        tags += [str(item.get("type")) for item in face_types if item.get("type")]
        primary = "mechanical_part"
        display = "机械零件"
        aliases_zh = ["零件", "机械零件"]
        aliases_en = ["part", "mechanical part"]
        summary = f"机械零件；{topology.get('faces', 0)} 个面，识别 {len(features)} 个几何特征。"
    else:
        analysis = content.get("analysis") or {}
        mesh = content.get("mesh") or {}
        derived = content.get("derived") or {}
        models = analysis.get("models") or {}
        physics = str(analysis.get("physics") or "simulation")
        traits = []
        if models.get("turbulence"):
            traits.append(f"湍流模型 {models['turbulence']}")
        multiphase = models.get("multiphase") or {}
        if multiphase.get("enabled"):
            traits.append(f"多相流 {multiphase.get('model', '')}".strip())
        if derived.get("droplet"):
            traits.append("可见液滴或相分布")
        if derived.get("result_tables"):
            traits.append("结构响应结果")
        primary = physics
        if "structural" in physics or "aeroelastic" in physics:
            display = "结构仿真"
            aliases_zh = ["有限元", "结构分析", "CAE"]
            aliases_en = ["structural simulation", "FEA", "CAE"]
        elif "multiphase" in physics:
            display = "多相流仿真"
            aliases_zh = ["流体仿真", "CFD", "多相流"]
            aliases_en = ["CFD", "multiphase flow"]
        else:
            display = "流体仿真"
            aliases_zh = ["流体仿真", "CFD"]
            aliases_en = ["fluid simulation", "CFD"]
        summary = (
            f"{analysis.get('solver', 'CAE')} 仿真；{mesh.get('cells', 0):,} 个单元，"
            f"{len(content.get('results') or [])} 个结果场。"
        )
    return {
        "primary_class": primary,
        "display_name_zh": display,
        "aliases_zh": aliases_zh,
        "aliases_en": aliases_en,
        "shape_traits": traits,
        "functional_guess": "",
        "confidence": None,
        "evidence": ["根据解析后的结构化字段生成"],
        "search_text_zh": "，".join([display, *aliases_zh, *traits]),
        "search_text_en": ", ".join([primary, *aliases_en]),
        "objects": [],
    }


class RuleBasedCaptioner:
    kind = "rule_based"
    provider = "local"

    def __init__(self, model: str = "rule_based/v2"):
        self.model = model

    def describe(self, data_type: str, content: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        profile = rule_profile(data_type, content)
        return self._summary(data_type, content), profile

    @staticmethod
    def _summary(data_type: str, content: dict[str, Any]) -> str:
        if data_type == "cad":
            parts = content.get("parts") or []
            if not parts:
                return "空模型。"
            part = parts[0]
            topology = part.get("topology") or {}
            return (
                f"零件含 {topology.get('solids', 0)} 个实体、"
                f"{topology.get('faces', 0)} 个面、"
                f"{len(part.get('features') or [])} 个识别特征。"
            )
        analysis = content.get("analysis") or {}
        mesh = content.get("mesh") or {}
        return (
            f"{analysis.get('solver', 'CAE')} 仿真，"
            f"{mesh.get('cells', 0):,} 个单元、{len(content.get('results') or [])} 个结果场。"
        )


class ApiCaptioner:
    kind = "vlm"

    def __init__(self, settings: VisualSettings):
        if not settings.api_key:
            raise ValueError("视觉 API 未配置 API Key")
        self.settings = settings
        self.model = settings.model
        self.provider = settings.provider

    def describe(
        self,
        data_type: str,
        content: dict[str, Any],
        views: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[str, dict[str, Any]]:
        images: list[dict[str, Any]] = []
        image_bytes = 0
        for view in (views or [])[:6]:
            file_path = view.get("path") or view.get("file")
            if not file_path or not os.path.isfile(file_path):
                continue
            mime = mimetypes.guess_type(file_path)[0] or "image/png"
            if not mime.startswith("image/"):
                continue
            with open(file_path, "rb") as handle:
                raw = handle.read()
            image_bytes += len(raw)
            encoded = base64.b64encode(raw).decode("ascii")
            images.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{encoded}",
                    "detail": "low",
                },
            })
        if not images:
            raise ValueError("没有可发送给视觉模型的渲染图片")

        prompt = self._prompt(data_type, content, views or [])
        messages = [{
            "role": "user",
            "content": [{"type": "text", "text": prompt}, *images],
        }]
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 1800,
        }
        endpoint = self.settings.base_url.rstrip("/") + "/chat/completions"
        request_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        last_error: Optional[Exception] = None
        for attempt_index in range(2):
            try:
                with span(
                    "llm.visual_describe.attempt",
                    kind="llm",
                    provider=self.provider,
                    model=self.model,
                    inputs={
                        "images": len(images),
                        "image_bytes": image_bytes,
                        "request_bytes": request_bytes,
                    },
                ) as attempt_span:
                    attempt_span.set_attributes(attempt=attempt_index + 1)
                    response = httpx.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {self.settings.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=self.settings.timeout,
                    )
                    response.raise_for_status()
                    response_payload = response.json()
                    message = response_payload["choices"][0]["message"]
                    text = message.get("content") or ""
                    if not isinstance(text, str):
                        text = json.dumps(text, ensure_ascii=False)
                    usage, usage_complete = extract_token_usage(response_payload)
                    attempt_span.set_token_usage(usage, complete=usage_complete)
                    attempt_span.set_outputs(
                        response_chars=len(text),
                        http_status=response.status_code,
                    )
                    parsed = self._parse_json(text)
                    profile = normalize_profile(parsed)
                    summary = self._summary(profile, data_type)
                    return summary, profile
            except Exception as exc:
                last_error = exc
        raise RuntimeError(str(last_error) if last_error else "视觉模型调用失败")

    @staticmethod
    def _prompt(data_type: str, content: dict[str, Any], views: list[dict[str, Any]]) -> str:
        if data_type == "cad":
            context = (
                "这是 CAD 零件的多视角渲染图。请依据外形判断它像什么零件，"
                "尤其关注是否有六边形外轮廓、中心通孔、法兰、槽、阵列孔等形状。"
            )
        else:
            context = (
                "这些是 CAE 仿真渲染图，可能包含网格、截面云图、曲线或结构变形。"
                "请描述仿真类型、可见物理现象和几何/结果图形特征。"
            )
        metadata = {
            "views": [
                {"view": item.get("view"), "path": item.get("path") or item.get("file")}
                for item in views
            ],
            "structured_hint": {
                "data_type": data_type,
                "parts": len(content.get("parts") or []),
                "cells": (content.get("mesh") or {}).get("cells"),
                "physics": (content.get("analysis") or {}).get("physics"),
            },
        }
        return (
            context
            + "\n只返回一个 JSON 对象，不要 Markdown，不要额外解释。字段如下：\n"
            "{\n"
            '  "primary_class": "英文稳定类别，如 nut/bolt/bracket/mesh/flow_field/structural_result/unknown",\n'
            '  "display_name_zh": "最可能的中文名称",\n'
            '  "aliases_zh": ["中文同义词"],\n'
            '  "aliases_en": ["English synonyms"],\n'
            '  "shape_traits": ["可直接用于相似形状检索的特征"],\n'
            '  "functional_guess": "可能功能",\n'
            '  "confidence": 0.0,\n'
            '  "evidence": ["来自具体视角的视觉依据"],\n'
            '  "search_text_zh": "适合全文检索的中文短语",\n'
            '  "search_text_en": "English search phrases",\n'
            '  "objects": [{"name_zh": "对象名", "name_en": "object", "count": 1, "confidence": 0.0}]\n'
            "}\n"
            "不确定时使用 unknown 并降低 confidence，不要编造不可见细节。\n"
            f"辅助元数据：{json.dumps(metadata, ensure_ascii=False)}"
        )

    @staticmethod
    def _summary(profile: dict[str, Any], data_type: str) -> str:
        display = profile.get("display_name_zh") or profile.get("primary_class") or "未知对象"
        traits = "、".join(profile.get("shape_traits") or [])[:180]
        prefix = "CAD" if data_type == "cad" else "CAE"
        return f"{prefix} 视觉判断：{display}" + (f"；主要特征：{traits}" if traits else "")

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("视觉模型未返回 JSON 对象")
        if isinstance(parsed.get("profile"), dict):
            parsed = parsed["profile"]
        return parsed


def make_captioner(
    data_type: str = "cad",
    *,
    settings: Optional[VisualSettings] = None,
    api_key: Optional[str] = None,
) -> RuleBasedCaptioner | ApiCaptioner:
    del data_type
    resolved = settings or resolve_visual_settings(api_key=api_key)
    if resolved.api_key:
        return ApiCaptioner(resolved)
    return RuleBasedCaptioner()


def describe(
    data_type: str,
    content: dict[str, Any],
    views: Optional[list[dict[str, Any]]] = None,
    captioner: Any = None,
) -> dict[str, Any]:
    analyzer = captioner or make_captioner(data_type)
    if analyzer.kind == "vlm":
        with span(
            "visual.describe",
            kind="llm",
            provider=getattr(analyzer, "provider", "deepseek"),
            model=getattr(analyzer, "model", "unknown"),
            inputs={"views": len(views or [])},
        ) as outer:
            try:
                summary, profile = analyzer.describe(data_type, content, views)
                outer.set_outputs(summary_chars=len(summary))
                return {
                    "kind": "vlm",
                    "summary": summary,
                    "model": getattr(analyzer, "model", "unknown"),
                    "provider": getattr(analyzer, "provider", "deepseek"),
                    "analyzed_at": _now(),
                    "profile": profile,
                    "error": None,
                }
            except Exception as exc:
                outer.set_status("fallback", error=str(exc))
                with span("visual.rule_fallback", kind="non_llm") as fallback_span:
                    fallback = RuleBasedCaptioner()
                    summary, profile = fallback.describe(data_type, content)
                    fallback_span.set_outputs(summary_chars=len(summary))
                return {
                    "kind": "rule_based_fallback",
                    "summary": summary,
                    "model": fallback.model,
                    "provider": "local",
                    "analyzed_at": _now(),
                    "profile": profile,
                    "error": str(exc),
                }
    with span(
        "visual.rule_describe",
        kind="non_llm",
        provider="local",
        model=getattr(analyzer, "model", "rule_based/v2"),
    ) as rule_span:
        summary, profile = analyzer.describe(data_type, content)
        rule_span.set_outputs(summary_chars=len(summary))
        return {
            "kind": "rule_based",
            "summary": summary,
            "model": getattr(analyzer, "model", "rule_based/v2"),
            "provider": "local",
            "analyzed_at": _now(),
            "profile": profile,
            "error": None,
        }
