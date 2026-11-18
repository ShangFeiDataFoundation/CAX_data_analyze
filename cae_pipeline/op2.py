"""Nastran OP2（二进制结果文件）读取。

OP2 是分层 Fortran 记录格式（table → record → 数据块），完整实现成本高；
这里复用 BSD 许可的 pyNastran 作为读取后端（可选依赖），
再把结果映射到与 Fluent / CGNS / BDF 相同的统一信封。

未安装 pyNastran 时抛出明确提示，不影响其他通道。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
import os
from collections import Counter
from typing import Any

import numpy as np
from cax_core.schema import SCHEMA_VERSION

PARSER_NAME = "cae_pipeline/0.2 (Nastran OP2 via pyNastran)"

# pyNastran 把结果按"单元类型 + 结果种类"存成属性：cquad4_stress / cbar_force / …
_TABLE_KINDS = (
    ("_strain_energy", "strain_energy"),
    ("_strain", "strain"),
    ("_stress", "stress"),
    ("_force", "force"),
)

# 已验证的分量标签（pyNastran 存储顺序）
_COMPONENT_LABELS = {
    ("CQUAD4", "stress"): ["fiber_distance", "normal_x", "normal_y", "shear_xy",
                           "angle", "major", "minor", "von_mises"],
}


class Op2Unavailable(RuntimeError):
    """缺少 pyNastran 后端。"""


def _sha256(path: str) -> dict[str, Any]:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"algorithm": "sha256", "value": h.hexdigest(), "bytes": os.path.getsize(path)}


def _jsonable(obj: Any) -> Any:
    """把 numpy 标量/数组转成原生类型（pydantic 的 Any 字段不会自动转）。"""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def _stats(values: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    q = np.percentile(arr, [1, 25, 50, 75, 99])
    return {
        "count": int(arr.size),
        "min": float(arr.min()), "max": float(arr.max()),
        "mean": float(arr.mean()), "std": float(arr.std()),
        "p1": float(q[0]), "p25": float(q[1]), "p50": float(q[2]),
        "p75": float(q[3]), "p99": float(q[4]),
    }


def _magnitude(data: Any) -> np.ndarray:
    arr = np.asarray(data)
    arr = np.abs(arr) if np.iscomplexobj(arr) else arr
    # 位移/特征向量最后一维是 6 个自由度，取前 3 个平移分量合成幅值
    if arr.ndim >= 3 and arr.shape[-1] >= 3:
        return np.sqrt(np.sum(arr[..., :3] ** 2, axis=-1))
    return np.abs(arr)


def _read_model(path: str):
    try:
        from pyNastran.op2.op2 import read_op2
    except ImportError as exc:  # pragma: no cover
        raise Op2Unavailable(
            "读取 OP2 需要可选依赖 pyNastran（以及 cpylog/scipy）。"
            "安装：pip install pyNastran cpylog scipy；未安装时其余通道不受影响。"
        ) from exc
    try:
        from cpylog import SimpleLogger
        log = SimpleLogger(level="error")
    except Exception:  # pragma: no cover
        log = None
    return read_op2(path, load_geometry=True, debug=False, build_dataframe=False,
                    mode="msc", log=log)


def _iter_result_tables(model):
    """枚举所有非空的应力/应变/单元力/应变能结果表。"""
    for attr in dir(model):
        if attr.startswith("_") or "." in attr:
            continue  # 跳过 force.xxx / stress.xxx 这类别名
        kind = next((k for suffix, k in _TABLE_KINDS if attr.endswith(suffix)), None)
        if kind is None:
            continue
        table = getattr(model, attr, None)
        if not isinstance(table, dict) or not table:
            continue
        for key, obj in table.items():
            data = getattr(obj, "data", None)
            if data is None or np.size(data) == 0:
                continue
            yield attr, kind, key, obj, data


def build_op2_json(path: str, out_dir: str | None = None, with_render: bool = True,
                   with_caption: bool = True, captioner: Any = None) -> dict[str, Any]:
    """把 Nastran OP2 解析为统一信封 JSON（结构/气动弹性结果）。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    model = _read_model(path)

    warnings: list[str] = []
    nodes = getattr(model, "nodes", {}) or {}
    xyz = np.array([np.asarray(n.xyz, dtype=float) for n in nodes.values()]) if nodes else np.zeros((0, 3))
    if xyz.size:
        bbox = {"xmin": float(xyz[:, 0].min()), "xmax": float(xyz[:, 0].max()),
                "ymin": float(xyz[:, 1].min()), "ymax": float(xyz[:, 1].max()),
                "zmin": float(xyz[:, 2].min()), "zmax": float(xyz[:, 2].max())}
    else:
        bbox = {"xmin": 0.0, "ymin": 0.0, "zmin": 0.0, "xmax": 0.0, "ymax": 0.0, "zmax": 0.0}
    extent = {"x": bbox["xmax"] - bbox["xmin"], "y": bbox["ymax"] - bbox["ymin"],
              "z": bbox["zmax"] - bbox["zmin"]}

    elements = getattr(model, "elements", {}) or {}
    elem_types = Counter(getattr(e, "type", "UNKNOWN") for e in elements.values())
    properties = getattr(model, "properties", {}) or {}
    materials = getattr(model, "materials", {}) or {}

    results: list[dict[str, Any]] = []
    derived: dict[str, Any] = {}
    result_tables: list[dict[str, Any]] = []

    # 位移表（静力/瞬态）
    displacements = getattr(model, "displacements", {}) or {}
    for subcase, obj in displacements.items():
        mag = _magnitude(obj.data)
        results.append({
            "field": f"displacement_subcase{subcase}",
            "semantic": "nodal_displacement_magnitude",
            "phase": 0,
            "location": "node",
            "stats": _stats(mag),
        })
        derived.setdefault("max_displacement", {})[f"subcase{subcase}"] = float(np.nanmax(mag)) if mag.size else None

    # 特征向量（模态/颤振）
    eigenvectors = getattr(model, "eigenvectors", {}) or {}
    modes = []
    for key, obj in eigenvectors.items():
        mode = key[1] if isinstance(key, tuple) and len(key) > 1 else key
        mag = _magnitude(obj.data)
        stats = _stats(mag)
        results.append({
            "field": f"eigenvector_mode_{mode}",
            "semantic": "mode_shape_magnitude",
            "phase": 0,
            "location": "node",
            "stats": stats,
        })
        modes.append({"mode": mode, "max": stats.get("max"), "node_count": int(mag.shape[-1]) if mag.ndim else 0})

    # 复特征值：imag = 圆频率，实部 = 阻尼
    eigen_tables = getattr(model, "eigenvalues", {}) or {}
    eigenvalues = []
    for subcase, obj in eigen_tables.items():
        arr = np.asarray(getattr(obj, "eigenvalues", []), dtype=complex).ravel()
        for i, lam in enumerate(arr):
            omega = abs(float(lam.imag))
            is_complex = omega > 0
            eigenvalues.append({
                "index": i + 1,
                "kind": "complex" if is_complex else "real",
                "real": float(lam.real),
                "imag": float(lam.imag),
                "frequency_hz": omega / (2 * math.pi) if is_complex else None,
                "damping_ratio": (-float(lam.real) / abs(lam)) if (is_complex and abs(lam)) else None,
            })
    if eigenvalues:
        derived["eigenvalues"] = eigenvalues
    if modes:
        derived["modes"] = modes

    # 应力 / 应变 / 单元力 / 应变能（各单元类型）
    for attr, kind, key, obj, data in _iter_result_tables(model):
        arr = np.asarray(data)
        mag = np.abs(arr) if np.iscomplexobj(arr) else arr
        # 最后一维是分量；统计按分量最大/整体分布
        stats = _stats(mag)
        if mag.ndim >= 3:
            ncomp = int(mag.shape[-1])
            comp_max = [float(np.nanmax(mag[..., i])) for i in range(ncomp)]
            stats["component_max"] = comp_max
        else:
            ncomp = 1
        base = attr
        for suffix, _ in _TABLE_KINDS:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        obj_name = base.upper()
        labels = _COMPONENT_LABELS.get((obj_name, kind))
        if labels and len(labels) == ncomp:
            stats["component_labels"] = labels
        location = "node" if "grid_point" in attr else "element"
        max_comp = int(np.nanargmax(stats["component_max"])) if stats.get("component_max") else None
        results.append({
            "field": f"{obj_name}_{kind}",
            "semantic": kind,
            "phase": 0,
            "location": location,
            "zone": obj_name,
            "components": labels if labels and len(labels) == ncomp else None,
            "component_count": ncomp,
            "stats": stats,
        })
        result_tables.append({
            "table": attr,
            "object": obj_name,
            "kind": kind,
            "subcase": key if isinstance(key, (int, str)) else str(key),
            "count": int(mag.shape[-2]) if mag.ndim >= 2 else int(mag.size),
            "components": ncomp,
            "max": stats.get("max"),
            "min": stats.get("min"),
            "max_component": (labels[max_comp] if (labels and max_comp is not None) else max_comp),
        })
    if result_tables:
        derived["result_tables"] = result_tables

    physics = "aeroelastic_flutter" if (eigenvalues and any(e["imag"] for e in eigenvalues)) else "structural_modal"
    if not eigenvectors and displacements:
        physics = "structural_static"
    if not nodes:
        warnings.append("OP2 中未读到 GRID 节点（load_geometry 未返回几何）")

    content: dict[str, Any] = {
        "analysis": {
            "physics": physics,
            "solver": "MSC Nastran OP2 (via pyNastran)",
            "models": {
                "subcases": sorted({k[0] for k in eigenvectors if isinstance(k, tuple)}),
                "mode_count": len(eigenvectors),
                "eigenvalue_count": len(eigenvalues),
            },
            "phases": [],
            "time": {},
            "units": {"note": "OP2 未显式声明单位，按求解器约定"},
            "operating_conditions": {},
        },
        "mesh": {
            "dimension": 3,
            "nodes": len(nodes),
            "faces": 0,
            "cells": int(getattr(model, "nelements", len(elements)) or 0),
            "boundary_faces": 0,
            "interior_faces": 0,
            "bbox": bbox,
            "extent": extent,
            "cell_types": dict(elem_types),
            "zones": {},
            "quality": {"faces_not_applicable": True, "result_side": True},
        },
        "setup": {
            "properties": {"count": len(properties), "types": dict(Counter(type(v).__name__ for v in properties.values()))},
            "materials": {"count": len(materials), "types": dict(Counter(type(v).__name__ for v in materials.values()))},
        },
        "field_catalog": {
            "displacements": list(displacements.keys()),
            "eigenvectors": [list(k) if isinstance(k, tuple) else k for k in eigenvectors.keys()],
            "eigenvalues": list(eigen_tables.keys()),
            "result_tables": [t["table"] for t in result_tables],
        },
        "results": results,
        "zone_results": [],
        "derived": derived,
        "rendered_views": [],
    }
    rendered_views: list[dict[str, Any]] = []
    if with_render:
        try:
            from .preview import preview_op2

            viz_dir = os.path.join(out_dir or os.path.dirname(path), "viz")
            rendered_views = preview_op2(path, viz_dir)
        except Exception as exc:
            warnings.append(f"渲染失败：{exc}")
    from cax_core.assets import render_assets

    content["rendered_views"] = render_assets(rendered_views, out_dir)
    if with_caption:
        from .multimodal import describe

        content["visual"] = describe(content, rendered_views, captioner=captioner)
    else:
        content["visual"] = None

    file_hash = _sha256(path)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "data_type": "cae",
        "source": {
            "primary_format": "NASTRAN_OP2",
            "files": [{
                "role": "result",
                "path": path,
                "format": "Nastran OP2",
                "bytes": os.path.getsize(path),
                "sha256": file_hash.get("value"),
                "hash_skipped": False,
                "hash_skip_reason": None,
            }],
            "parser": {"name": "cae_pipeline.op2", "version": "0.2"},
            "parsed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "solver": "MSC Nastran OP2",
            "dataset": None,
        },
        "meta": {
            "validation": {"is_valid": True, "warnings": warnings, "errors": []},
            "provenance": {"source_files": [path],
                           "steps": ["op2_open", "read_geom", "read_results",
                                     "eigen_extract", "render_views", "visual_describe",
                                     "schema_validate"]},
            "capabilities": {
                "geometry": bool(nodes),
                "mesh": True,
                "setup": bool(content.get("setup")),
                "results": bool(results),
                "render": bool(rendered_views),
                "vision": bool(content.get("visual")),
            },
            "tags": ["nastran", "op2", "structural"],
            "domain": "commercial_aircraft",
        },
        "content": content,
    }

    doc = _jsonable(doc)
    from .schema import dump_cae
    from cax_core.classification import ensure_classification

    ensure_classification(doc)
    try:
        return dump_cae(doc)
    except Exception as exc:  # pragma: no cover
        doc["meta"]["validation"] = {
            "is_valid": False,
            "warnings": warnings,
            "errors": [f"schema_error: {exc}"],
        }
        return doc
