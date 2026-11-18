"""CAD 解析主流程：STEP / IGES -> 结构化 JSON（统一信封 + 校验 + 溯源）。"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from typing import Any

from .assembly import extract_assembly
from .features import recognize_features
from .geometry import extract_geometry, read_shape
from .iges_meta import parse_iges_header
from .multimodal import describe
from .render import render_views
from .step_meta import parse_step_header
from cax_core.assets import render_assets
from cax_core.classification import ensure_classification
from cax_core.perf import span
from cax_core.schema import SCHEMA_VERSION

try:
    from .schema import dump_cad

    _SCHEMA_OK = True
except Exception:  # pragma: no cover - schema 依赖缺失时降级
    _SCHEMA_OK = False

SCHEMA_VERSION_CAD = SCHEMA_VERSION
PARSER_NAME = "cad_pipeline"
PARSER_VERSION = "0.4 (OCP; STEP+IGES)"
PROVENANCE_STEPS = ["extract_geometry", "recognize_features", "extract_assembly",
                    "render_views", "visual_describe", "schema_validate"]

# 交换格式的统一命名
FORMAT_BY_EXT = {
    "stp": "STEP",
    "step": "STEP",
    "igs": "IGES",
    "iges": "IGES",
}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_header_meta(path: str, fmt: str) -> dict[str, Any]:
    """读取文件头段元数据（STEP 用 UTF-8/ASCII，IGES 用 latin-1）。"""
    encoding = "latin-1" if fmt == "IGES" else "utf-8"
    with open(path, "r", encoding=encoding, errors="ignore") as f:
        header_text = f.read(200_000)
    return parse_iges_header(header_text) if fmt == "IGES" else parse_step_header(header_text)


def build_cad_json(
    step_path: str,
    out_dir: str | None = None,
    with_render: bool = True,
    with_caption: bool = True,
    captioner: Any = None,
) -> dict[str, Any]:
    """解析单个 STEP/IGES，返回符合统一信封的 JSON 字典。"""
    step_path = os.path.abspath(step_path)
    if not os.path.isfile(step_path):
        raise FileNotFoundError(step_path)

    ext = os.path.splitext(step_path)[1].lstrip(".").lower()
    if ext not in FORMAT_BY_EXT:
        raise ValueError(f"不支持的文件格式：.{ext}（当前支持 .step/.stp/.igs/.iges）")
    fmt = FORMAT_BY_EXT[ext]
    source_bytes = os.path.getsize(step_path)
    with span(
        "cad.read_header",
        inputs={"files": 1, "bytes": source_bytes, "format": fmt},
    ) as header_span:
        meta = _read_header_meta(step_path, fmt)
        header_span.set_outputs(fields=len(meta))

    with span("cad.read_shape", inputs={"files": 1, "bytes": source_bytes}) as shape_span:
        shape = read_shape(step_path)
        shape_span.set_outputs(loaded=True)

    with span("cad.extract_geometry") as geometry_span:
        geometry = extract_geometry(shape)
        geometry_span.set_outputs(
            faces=(geometry.get("topology") or {}).get("faces"),
            solids=(geometry.get("topology") or {}).get("solids"),
            edges=(geometry.get("topology") or {}).get("edges"),
        )

    with span(
        "cad.recognize_features",
        inputs={"faces": (geometry.get("topology") or {}).get("faces")},
    ) as feature_span:
        features = recognize_features(geometry)
        feature_span.set_outputs(features=len(features))

    parent_dir = os.path.basename(os.path.dirname(step_path))
    model_id = parent_dir if (parent_dir and not parent_dir.startswith(".")) else None
    raw_name = meta.get("name")
    path_like = bool(raw_name and ("/" in raw_name or "\\" in raw_name))
    unusable = bool(raw_name and raw_name.strip().lower() in ("unknown", "unnamed", "default", "none"))
    display_name = (raw_name if (raw_name and not path_like and not unusable)
                    else (model_id or os.path.splitext(os.path.basename(step_path))[0]))
    part_id = model_id or "P1"

    parts = [
        {
            "id": part_id,
            "name": display_name,
            "bbox": geometry["bbox"],
            "mass_props": geometry["mass_props"],
            "is_closed": geometry["topology"]["shells"] > 0,
            "topology": geometry["topology"],
            "face_types": geometry["face_types"],
            "features": features,
        }
    ]

    content: dict[str, Any] = {
        "product": {
            "name": meta.get("name"),
            "author": meta.get("author"),
            "units": meta.get("units"),
            "schema": meta.get("schema"),
            "standard": meta.get("implementation_level"),
        },
        "parts": parts,
        "assembly": None,
    }
    with span("cad.extract_assembly") as assembly_span:
        content["assembly"] = extract_assembly(shape, part_id)
        assembly_span.set_outputs(
            instances=len(
                (content.get("assembly") or {}).get("instances") or []
            )
        )

    rendered_views: list[dict[str, Any]] = []
    if with_render:
        render_dir = (os.path.join(out_dir, "renders") if out_dir
                      else os.path.join(os.path.dirname(step_path), "renders"))
        prefix = os.path.splitext(os.path.basename(step_path))[0]
        with span("cad.render_views") as render_span:
            try:
                rendered_views = render_views(shape, render_dir, prefix)
                render_span.set_outputs(views=len(rendered_views))
            except Exception as exc:  # pragma: no cover
                render_span.set_status("warning", error=str(exc))
                print(f"[pipeline] 渲染失败：{exc}")
                rendered_views = []
    content["rendered_views"] = render_assets(rendered_views, out_dir)

    if with_caption:
        content["visual"] = describe(content, rendered_views, captioner=captioner)
    else:
        content["visual"] = None

    with span(
        "source.sha256",
        inputs={"files": 1, "bytes": source_bytes},
    ) as hash_span:
        file_hash = _sha256(step_path)
        hash_span.set_outputs(hash_chars=len(file_hash))
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_CAD,
        "data_type": "cad",
        "source": {
            "primary_format": fmt,
            "files": [{
                "role": "model",
                "path": step_path,
                "format": fmt,
                "bytes": source_bytes,
                "sha256": file_hash,
                "hash_skipped": False,
                "hash_skip_reason": None,
            }],
            "parser": {"name": PARSER_NAME, "version": PARSER_VERSION},
            "parsed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "solver": None,
            "dataset": None,
        },
        "meta": {
            "validation": {"is_valid": True, "warnings": [], "errors": []},
            "provenance": {"source_files": [step_path],
                           "steps": [f"read_{fmt.lower()}", *PROVENANCE_STEPS]},
            "capabilities": {
                "geometry": True,
                "mesh": False,
                "setup": False,
                "results": False,
                "render": bool(rendered_views),
                "vision": bool(content.get("visual")),
            },
            "tags": [fmt.lower()],
            "domain": "commercial_aircraft",
        },
        "content": content,
    }

    with span("classification.build") as classification_span:
        ensure_classification(doc)
        classification_span.set_outputs(
            facets=len((doc["meta"]["classification"] or {}).get("facets") or []),
            categories=len((doc["meta"]["classification"] or {}).get("summary") or []),
        )
    if _SCHEMA_OK:
        with span("schema.validate") as schema_span:
            try:
                doc = dump_cad(doc)  # 校验并规范化
                schema_span.set_outputs(valid=True)
            except Exception as exc:
                schema_span.set_status("error", error=str(exc))
                doc["meta"]["validation"] = {
                    "is_valid": False,
                    "warnings": [],
                    "errors": [f"schema_error: {exc}"],
                }
    return doc


def parse_cad_step(
    step_path: str,
    out_dir: str | None = None,
    with_render: bool = True,
    with_caption: bool = True,
    write_json: bool = True,
    captioner: Any = None,
) -> dict[str, Any]:
    """便捷入口：解析并（可选）写出 JSON 文件，返回字典。"""
    result = build_cad_json(
        step_path,
        out_dir=out_dir,
        with_render=with_render,
        with_caption=with_caption,
        captioner=captioner,
    )
    if write_json:
        base = os.path.splitext(os.path.basename(step_path))[0]
        out = os.path.join(out_dir or os.path.dirname(step_path), f"{base}_cad.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        result["_written_to"] = out
    return result


# 兼容旧入口名：现在同时支持 STEP 与 IGES
parse_cad_file = parse_cad_step
