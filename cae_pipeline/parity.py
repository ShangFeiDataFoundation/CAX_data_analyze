"""CAE 跨格式一致性检查：同一网格在两种格式下的节点/单元/包围盒/单元类型比对。"""

from __future__ import annotations

from typing import Any

# BDF / CGNS 单元类型别名（比较前归一化）
TYPE_ALIASES = {
    "CQUAD4": "QUAD_4", "CQUAD": "QUAD_4", "CQUAD8": "QUAD_8",
    "CTRIA3": "TRI_3", "CTRIA6": "TRI_6", "CTRIAR": "TRI_3", "CSHEAR": "QUAD_4",
    "CBAR": "BAR_2", "CBEAM": "BAR_2", "CROD": "BAR_2", "CONROD": "BAR_2",
    "CTUBE": "BAR_2", "CBEND": "BAR_2",
    "CTETRA": "TETRA_4", "CHEXA": "HEXA_8", "CPENTA": "PENTA_6", "CPYRA": "PYRA_5",
}


def _norm_types(cell_types: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in (cell_types or {}).items():
        key = TYPE_ALIASES.get(k, k)
        out[key] = out.get(key, 0) + int(v)
    return out


def compare_cae_documents(doc_a: dict[str, Any], doc_b: dict[str, Any],
                          bbox_rtol: float = 1e-6) -> dict[str, Any]:
    """比较两份 CAE JSON 的网格层信息。"""
    a, b = doc_a["content"]["mesh"], doc_b["content"]["mesh"]
    diffs: list[str] = []
    warnings: list[str] = []

    for key in ("nodes", "cells"):
        if int(a.get(key) or 0) != int(b.get(key) or 0):
            diffs.append(f"mesh.{key}: {a.get(key)} != {b.get(key)}")

    ta, tb = _norm_types(a.get("cell_types") or {}), _norm_types(b.get("cell_types") or {})
    if ta != tb:
        diffs.append(f"cell_types: {ta} != {tb}")

    ext = max(
        abs(a["bbox"][k] - b["bbox"][k]) for k in ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax")
    ) if a.get("bbox") and b.get("bbox") else 0.0
    scale = max(
        *(abs(a["bbox"][k]) for k in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")),
        *(abs(b["bbox"][k]) for k in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")),
        1e-12,
    ) if a.get("bbox") and b.get("bbox") else 1.0
    if ext / scale > bbox_rtol:
        diffs.append(f"bbox 最大偏差 {ext:.3g}（相对 {ext/scale:.2e}）")

    # 面数语义因格式而异（BDF 无面概念），只作提示
    if int(a.get("boundary_faces") or 0) != int(b.get("boundary_faces") or 0):
        warnings.append(
            f"boundary_faces: {a.get('boundary_faces')} != {b.get('boundary_faces')}（格式语义差异，不计入不一致）"
        )

    return {
        "format_a": doc_a["source"]["format"],
        "format_b": doc_b["source"]["format"],
        "file_a": doc_a["source"]["filename"],
        "file_b": doc_b["source"]["filename"],
        "match": not diffs,
        "diffs": diffs,
        "warnings": warnings,
        "nodes": (a.get("nodes"), b.get("nodes")),
        "cells": (a.get("cells"), b.get("cells")),
    }


def format_report(result: dict[str, Any]) -> str:
    head = "一致" if result["match"] else f"不一致（{len(result['diffs'])} 项）"
    lines = [
        f"{result['file_a']} [{result['format_a']}]  vs  {result['file_b']} [{result['format_b']}]: {head}",
        f"  节点 {result['nodes'][0]} / {result['nodes'][1]}；单元 {result['cells'][0]} / {result['cells'][1]}",
    ]
    lines.extend(f"  - {d}" for d in result["diffs"])
    lines.extend(f"  ! {w}" for w in result.get("warnings", []))
    return "\n".join(lines)
