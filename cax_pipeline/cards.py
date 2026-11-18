"""Compact, format-aware card facts derived from canonical CAX JSON."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

PHYSICS_LABELS = {
    "fluid_flow": "流体仿真",
    "multiphase_flow": "多相流仿真",
    "structural_static": "结构静力",
    "structural_modal": "结构模态",
    "aeroelastic_flutter": "气动弹性 / 颤振",
    "structural": "结构分析",
    "aeroelastic": "气动弹性",
}

FACE_LABELS = {
    "PLANE": "平面",
    "CYLINDER": "圆柱面",
    "CONE": "圆锥面",
    "SPHERE": "球面",
    "TORUS": "圆环面",
    "BSPLINE": "B 样条面",
    "REVOLUTION": "旋转面",
}

FEATURE_LABELS = {
    "cylindrical": "圆柱特征",
    "pattern": "阵列",
    "round": "圆角",
    "chamfer": "倒角",
}

UNIT_LABELS = {
    "MILLIMETRE": "mm",
    "MILLIMETER": "mm",
    "MM": "mm",
    "CENTIMETRE": "cm",
    "CENTIMETER": "cm",
    "METER": "m",
    "METRE": "m",
    "INCH": "in",
    "IN": "in",
}


def _number(value: Any, unit: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    magnitude = abs(number)
    if magnitude >= 1e6 or (0 < magnitude < 0.01):
        text = f"{number:.3e}"
    elif magnitude >= 1000:
        text = f"{number:,.1f}".rstrip("0").rstrip(".")
    else:
        text = f"{number:,.3f}".rstrip("0").rstrip(".")
    return f"{text} {unit}".strip()


def _metric(label: str, value: Any) -> dict[str, str] | None:
    text = str(value or "").strip()
    return {"label": label, "value": text} if text else None


def _count(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 10_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:,}"


def _dedupe(values: list[str], limit: int = 5) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))[:limit]


def _cad_card(doc: dict[str, Any]) -> dict[str, Any]:
    content = doc.get("content") or {}
    product = content.get("product") or {}
    parts = content.get("parts") or []
    part = parts[0] if parts else {}
    bbox = part.get("bbox") or {}
    topology = part.get("topology") or {}
    mass = part.get("mass_props") or {}
    raw_unit = str(product.get("units") or "").strip()
    unit = UNIT_LABELS.get(raw_unit.upper(), raw_unit)
    dims = [
        float(bbox.get("xmax", 0)) - float(bbox.get("xmin", 0)),
        float(bbox.get("ymax", 0)) - float(bbox.get("ymin", 0)),
        float(bbox.get("zmax", 0)) - float(bbox.get("zmin", 0)),
    ]
    metrics = [
        _metric("尺寸", " × ".join(_number(value) for value in dims) + (f" {unit}" if unit else "")),
        _metric("体积", _number(mass.get("volume"), f"{unit}³" if unit else "")),
        _metric(
            "拓扑",
            f"{int(topology.get('faces') or 0)} 面 · {int(topology.get('solids') or 0)} 实体",
        ),
    ]
    features = part.get("features") or []
    holes = [
        item for item in features
        if (item.get("params") or {}).get("subtype") == "hole"
    ]
    if holes:
        radii = sorted({
            float(item["params"]["radius"])
            for item in holes
            if (item.get("params") or {}).get("radius") is not None
        })
        value = f"{len(holes)} 个孔"
        if radii:
            value += f" · 半径 {_number(radii[0], unit)}"
        metrics.append(_metric("孔特征", value))
    else:
        metrics.append(_metric("识别特征", f"{len(features)} 个"))
    metrics.append(
        _metric("表面积", _number(mass.get("surface_area"), f"{unit}²" if unit else ""))
    )

    visual = (content.get("visual") or {}).get("profile") or {}
    tags = list(visual.get("shape_traits") or [])[:3]
    tags += [
        FACE_LABELS.get(item.get("type"), str(item.get("type")))
        for item in (part.get("face_types") or [])[:3]
    ]
    tags += [
        FEATURE_LABELS.get(item.get("type"), str(item.get("type")))
        for item in features[:3]
    ]
    return {
        "metrics": [item for item in metrics if item][:4],
        "tags": _dedupe(tags, 5),
    }


def _dict_card_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        cards = value.get("cards")
        if isinstance(cards, list):
            return len(cards)
        if isinstance(cards, (int, float)):
            return int(cards)
        return 0
    return 0


def _cae_card(doc: dict[str, Any]) -> dict[str, Any]:
    content = doc.get("content") or {}
    analysis = content.get("analysis") or {}
    mesh = content.get("mesh") or {}
    setup = content.get("setup") or {}
    derived = content.get("derived") or {}
    physics = str(analysis.get("physics") or "")
    models = analysis.get("models") or {}
    cells = int(mesh.get("cells") or 0)
    nodes = int(mesh.get("nodes") or 0)
    field_count = len(content.get("results") or [])
    zones = mesh.get("zones") or {}

    metrics = [
        _metric("网格", f"{_count(cells)} 单元 · {_count(nodes)} 节点"),
        _metric("结果场", f"{field_count} 个"),
        _metric("分区", f"{len(zones)} 个" if zones else ""),
    ]

    droplet = derived.get("droplet")
    if droplet:
        metrics.append(_metric(
            "铺展系数 D/D0",
            _number(droplet.get("spreading_ratio")),
        ))
        metrics.append(_metric(
            "液滴直径",
            _number(
                (droplet.get("diameter_m") or 0) * 1e6,
                "µm",
            ),
        ))
    else:
        result_tables = derived.get("result_tables") or []
        stress = next(
            (
                item for item in result_tables
                if item.get("max_component") == "von_mises" or item.get("kind") == "stress"
            ),
            None,
        )
        if stress:
            metrics.append(_metric("最大应力", _number(stress.get("max"))))
        displacement = next(
            iter((derived.get("max_displacement") or {}).values()),
            None,
        )
        if displacement is not None:
            metrics.append(_metric("最大位移", _number(displacement)))
        eigenvalues = derived.get("eigenvalues") or []
        frequencies = [
            float(item["frequency_hz"])
            for item in eigenvalues
            if item.get("frequency_hz") is not None
        ]
        if frequencies:
            metrics.append(_metric(
                "频率范围",
                f"{_number(min(frequencies))}–{_number(max(frequencies), 'Hz')}",
            ))
        if not stress and not displacement and not frequencies:
            constraints = _dict_card_count(setup.get("constraints"))
            loads = _dict_card_count(setup.get("loads"))
            aero = _dict_card_count(setup.get("aero"))
            if constraints or loads:
                metrics.append(_metric("约束 / 载荷", f"{constraints} / {loads}"))
            elif aero:
                metrics.append(_metric("气动卡片", f"{aero} 个"))

    tags = [PHYSICS_LABELS.get(physics, physics)]
    if models.get("turbulence"):
        tags.append(str(models["turbulence"]))
    multiphase = models.get("multiphase") or {}
    if multiphase.get("enabled"):
        tags.append(f"{multiphase.get('model', '多相')} · {multiphase.get('phase_count', 0)} 相")
    if analysis.get("time", {}).get("transient"):
        tags.append("瞬态")
    elif analysis.get("time"):
        tags.append("稳态")
    phases = analysis.get("phases") or []
    tags += [str(item.get("material")) for item in phases if item.get("material")]
    cell_types = Counter(mesh.get("cell_types") or {})
    tags += [str(name).upper() for name, _count in cell_types.most_common(2)]
    solver = str(analysis.get("solver") or "")
    solver = (
        solver.replace(" (input deck)", "")
        .replace(" (via pyNastran)", "")
        .replace("ANSYS ", "")
    )
    if solver:
        tags.append(solver)
    return {
        "metrics": [item for item in metrics if item][:4],
        "tags": _dedupe(tags, 5),
    }


def build_card(doc: dict[str, Any]) -> dict[str, Any]:
    if doc.get("data_type") == "cad":
        card = _cad_card(doc)
    else:
        card = _cae_card(doc)
    warnings = (doc.get("meta") or {}).get("validation", {}).get("warnings") or []
    card["warning_count"] = len(warnings)
    return card
