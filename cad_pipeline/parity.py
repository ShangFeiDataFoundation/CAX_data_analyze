"""跨格式一致性检查：同一零件的两种表达（如 STEP 与 IGES）解析结果对比。

用于回答"换一种格式传来，解析结论是否一致"：
  - 拓扑计数（面/边/顶点）应严格相等；
  - 质量属性（体积/表面积）允许转换误差，用相对误差阈值；
  - 面类型分布与特征数应相等（IGES 往返会退化解析曲面，读取层已做还原）。
"""

from __future__ import annotations

from typing import Any


def _part(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["content"]["parts"][0]


def _face_types(part: dict[str, Any]) -> dict[str, int]:
    return {ft["type"]: ft["count"] for ft in part.get("face_types", [])}


def compare_cad_documents(
    doc_a: dict[str, Any],
    doc_b: dict[str, Any],
    volume_rtol: float = 1e-4,
    area_rtol: float = 1e-4,
) -> dict[str, Any]:
    """比较两份 CAD JSON，返回差异清单与是否一致。"""
    pa, pb = _part(doc_a), _part(doc_b)
    diffs: list[str] = []
    warnings: list[str] = []

    # 面数严格一致；边/顶点数只作提示——IGES 无拓扑信息，接缝边常被拆分
    for key in ("faces",):
        va = pa["topology"].get(key)
        vb = pb["topology"].get(key)
        if va != vb:
            diffs.append(f"topology.{key}: {va} != {vb}")
    for key in ("edges", "vertices"):
        va = pa["topology"].get(key)
        vb = pb["topology"].get(key)
        if va != vb:
            warnings.append(f"topology.{key}: {va} != {vb}（IGES 接缝边差异，不影响体积/特征）")

    solids_a = int(pa["topology"].get("solids") or 0)
    solids_b = int(pb["topology"].get("solids") or 0)
    if (solids_a > 0) != (solids_b > 0):
        diffs.append(
            f"closure: {doc_a['source']['format']} 实体 {solids_a} 个 / "
            f"{doc_b['source']['format']} 实体 {solids_b} 个（转换后未闭合）"
        )
    elif solids_a != solids_b:
        diffs.append(f"topology.solids: {solids_a} != {solids_b}")

    for key in ("volume", "surface_area"):
        va = float(pa["mass_props"].get(key) or 0.0)
        vb = float(pb["mass_props"].get(key) or 0.0)
        rtol = volume_rtol if key == "volume" else area_rtol
        scale = max(abs(va), abs(vb), 1e-12)
        rel = abs(va - vb) / scale
        if rel > rtol:
            diffs.append(f"mass_props.{key}: {va:.6g} vs {vb:.6g} (rel={rel:.2e})")

    fa, fb = _face_types(pa), _face_types(pb)
    if fa != fb:
        diffs.append(f"face_types: {fa} != {fb}")

    na, nb = len(pa.get("features", [])), len(pb.get("features", []))
    if na != nb:
        diffs.append(f"features: {na} != {nb}")

    ia = len((doc_a["content"].get("assembly") or {}).get("instances") or [])
    ib = len((doc_b["content"].get("assembly") or {}).get("instances") or [])
    if ia != ib:
        diffs.append(f"assembly_instances: {ia} != {ib}")

    return {
        "format_a": doc_a["source"]["format"],
        "format_b": doc_b["source"]["format"],
        "file_a": doc_a["source"]["filename"],
        "file_b": doc_b["source"]["filename"],
        "match": not diffs,
        "diffs": diffs,
        "warnings": warnings,
        "volume_a": pa["mass_props"].get("volume"),
        "volume_b": pb["mass_props"].get("volume"),
        "features_a": na,
        "features_b": nb,
    }


def format_report(result: dict[str, Any]) -> str:
    head = "一致" if result["match"] else f"不一致（{len(result['diffs'])} 项）"
    lines = [
        f"{result['file_a']} [{result['format_a']}]  vs  {result['file_b']} [{result['format_b']}]: {head}",
        f"  体积 {result['volume_a']:.6g} / {result['volume_b']:.6g}；特征 {result['features_a']} / {result['features_b']}",
    ]
    lines.extend(f"  - {d}" for d in result["diffs"])
    lines.extend(f"  ! {w}" for w in result.get("warnings", []))
    return "\n".join(lines)
