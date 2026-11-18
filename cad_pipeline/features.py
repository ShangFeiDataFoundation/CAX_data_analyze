"""特征识别（几何级启发式，原型水平）。

策略：
  - 把共轴、等半径的圆柱面簇聚合为一个"圆柱特征"，给出半径/轴向/深度/位置；
  - 深径比启发式区分孔(hole) vs 凸台(boss)；
  - >=2 个同轴同径圆柱特征 -> 阵列(pattern)；
  - 小半径圆柱面（相对包围盒）计为圆角(round)代理；
  - CONE 面计为倒角(chamfer)代理。
语义级设计意图识别为后续研究点，这里仅输出置信度。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _key(v: float, tol: float = 1.0) -> int:
    return round(v / tol)


def _axis_key(c: dict[str, Any], tol: float = 0.1) -> tuple[int, int, int]:
    """轴向的无向键：先统一符号再分箱。

    同一圆柱面在不同格式/内核中可能以相反方向表达（实测 STEP 全为 -Y、
    IGES 往返后同一孔的两个半面分别为 +Y/-Y），不归一化会把一个孔拆成两个特征。
    """
    ax = _canonical_axis(c)
    return (_key(ax[0], tol), _key(ax[1], tol), _key(ax[2], tol))


def _canonical_axis(c: dict[str, Any]) -> list[float]:
    v = [_unit([c["axis_x"], c["axis_y"], c["axis_z"]])][0]
    for comp in v:
        if abs(comp) > 1e-9:
            if comp < 0:
                v = [-x for x in v]
            break
    return v


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cluster_cylinders(cylinders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (轴向, 半径, 位置) 聚合圆柱面。

    位置用半径量级的容差分箱：同一轴线上的多个面（同一孔/轴）合并；
    不同位置（平行错开的孔/槽端头）分离，从而支持 pattern/slot 检测。
    """
    groups: dict[tuple[tuple[int, int, int], int, tuple[int, int, int]], list[dict[str, Any]]] = {}
    for c in cylinders:
        axk = _axis_key(c)
        rk = round(c["radius"] / 1e-3)
        tol = max(abs(c["radius"]) * 0.5, 1e-3)
        lk = (_key(c["loc_x"], tol), _key(c["loc_y"], tol), _key(c["loc_z"], tol))
        gkey = (axk, rk, lk)
        groups.setdefault(gkey, []).append(c)

    clusters: list[dict[str, Any]] = []
    for (_, _, _), members in groups.items():
        radius = float(np.mean([m["radius"] for m in members]))
        # 用归一化后的轴向求均值；直接平均 ±Y 会得到零向量
        ax = [float(np.mean([_canonical_axis(m)[k] for m in members])) for k in range(3)]
        ax = _unit(ax)
        loc = [float(np.mean([m[f"loc_{a}"] for m in members])) for a in "xyz"]
        depth = float(max(m["depth"] for m in members))
        clusters.append(
            {
                "radius": radius,
                "axis": [round(a, 6) for a in ax],
                "location": [round(a, 6) for a in loc],
                "depth": depth,
                "face_count": len(members),
            }
        )
    clusters.sort(key=lambda c: -c["radius"])
    return clusters


def _classify(cluster: dict[str, Any]) -> tuple[str, float]:
    """深径比启发式：孔 vs 凸台 vs 中性。"""
    r = cluster["radius"]
    d = cluster["depth"]
    if r <= 0:
        return "cylindrical", 0.3
    ratio = d / r
    if ratio >= 2.0:
        return "hole", 0.7
    if ratio <= 0.6 and d > 0:
        return "boss", 0.6
    return "cylindrical", 0.4


def recognize_features(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    """主入口：返回符合 Feature schema 的特征列表。"""
    bbox = geometry.get("bbox", {})
    diag = _bbox_diag(bbox)
    cylinders = geometry.get("cylinders", [])
    face_types = {ft["type"]: ft["count"] for ft in geometry.get("face_types", [])}
    features: list[dict[str, Any]] = []

    clusters = _cluster_cylinders(cylinders)
    for i, cl in enumerate(clusters):
        subtype, conf = _classify(cl)
        features.append(
            {
                "id": f"F{i:04d}",
                "type": "cylindrical",
                "confidence": round(conf, 2),
                "params": {
                    "subtype": subtype,
                    "radius": round(cl["radius"], 6),
                    "axis": cl["axis"],
                    "location": cl["location"],
                    "depth": round(cl["depth"], 6),
                },
                "face_count": cl["face_count"],
            }
        )

    # 槽对：平行同径轴距≈2r
    slots = _detect_slots(clusters)
    slot_sigs = {(f["params"]["radius"], _axis_key({"axis_x": f["params"]["axis"][0],
                                                    "axis_y": f["params"]["axis"][1],
                                                    "axis_z": f["params"]["axis"][2]}))
                 for f in slots}
    # 阵列：>=2 个同向同径圆柱特征（槽对不再重复计为阵列）
    patterns = _detect_patterns(clusters)
    for p in patterns:
        sig = (p["params"]["radius"], _axis_key({"axis_x": p["params"]["axis"][0],
                                                 "axis_y": p["params"]["axis"][1],
                                                 "axis_z": p["params"]["axis"][2]}))
        if p["params"]["count"] >= 3 or sig not in slot_sigs:
            features.append(p)

    for s in slots:
        features.append(s)

    # 圆角代理：小半径圆柱面
    small = [c for c in cylinders if diag > 0 and c["radius"] <= diag * 0.02]
    if small:
        features.append(
            {
                "id": f"F{len(features):04d}",
                "type": "round",
                "confidence": 0.4,
                "params": {"radius_max": round(max(c["radius"] for c in small), 6), "count": len(small)},
                "face_count": len(small),
            }
        )

    # 倒角代理：CONE 面
    cone_count = face_types.get("CONE", 0)
    if cone_count:
        features.append(
            {
                "id": f"F{len(features):04d}",
                "type": "chamfer",
                "confidence": 0.4,
                "params": {"count": cone_count},
                "face_count": cone_count,
            }
        )

    return features


def _bbox_diag(bbox: dict[str, float]) -> float:
    try:
        dx = bbox["xmax"] - bbox["xmin"]
        dy = bbox["ymax"] - bbox["ymin"]
        dz = bbox["zmax"] - bbox["zmin"]
        return math.sqrt(dx * dx + dy * dy + dz * dz)
    except Exception:
        return 0.0


def _detect_patterns(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同向同径圆柱簇 >=2 且平面位置不同 -> 阵列（如螺栓孔组）。"""
    by_key: dict[tuple[tuple[int, int, int], int], list[dict[str, Any]]] = {}
    for c in clusters:
        axk = _axis_key({"axis_x": c["axis"][0], "axis_y": c["axis"][1], "axis_z": c["axis"][2]})
        by_key.setdefault((axk, round(c["radius"] / 1e-3)), []).append(c)

    patterns: list[dict[str, Any]] = []
    for (_, _), members in by_key.items():
        if len(members) < 2:
            continue
        # 平面内二维坐标（去轴向后）
        axis = np.array(members[0]["axis"])
        perp_a = np.cross(axis, [1.0, 0.0, 0.0])
        if np.linalg.norm(perp_a) < 1e-6:
            perp_a = np.cross(axis, [0.0, 1.0, 0.0])
        perp_a = perp_a / (np.linalg.norm(perp_a) + 1e-12)
        perp_b = np.cross(axis, perp_a)
        coords = []
        for m in members:
            loc = np.array(m["location"])
            coords.append([loc @ perp_a, loc @ perp_b])
        coords = np.array(coords)
        spread = coords[:, 0].std(), coords[:, 1].std()
        centroid = coords.mean(axis=0)
        patterns.append(
            {
                "id": f"P{len(patterns):03d}",
                "type": "pattern",
                "confidence": 0.7,
                "params": {
                    "kind": "coaxial_pattern",
                    "count": len(members),
                    "radius": round(members[0]["radius"], 6),
                    "axis": members[0]["axis"],
                    "spread": [round(float(s), 6) for s in spread],
                    "centroid_2d": [round(float(v), 6) for v in centroid],
                },
                "face_count": sum(m["face_count"] for m in members),
            }
        )
    return patterns


def _detect_slots(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """平行、同径、轴距≈2r 的两个圆柱端头 -> 槽。"""
    by_key: dict[tuple[tuple[int, int, int], int], list[dict[str, Any]]] = {}
    for c in clusters:
        axk = _axis_key({"axis_x": c["axis"][0], "axis_y": c["axis"][1], "axis_z": c["axis"][2]})
        by_key.setdefault((axk, round(c["radius"] / 1e-3)), []).append(c)

    slots: list[dict[str, Any]] = []
    for (_, _), members in by_key.items():
        if len(members) < 2:
            continue
        # 两两配对，找轴距≈2r 的对
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                da = np.array(a["location"])
                db = np.array(b["location"])
                sep = float(np.linalg.norm(da - db))
                r = a["radius"]
                if abs(sep - 2 * r) <= max(0.15 * r, 1e-3):
                    slots.append(
                        {
                            "id": f"S{len(slots):03d}",
                            "type": "slot",
                            "confidence": 0.55,
                            "params": {
                                "radius": round(r, 6),
                                "axis": a["axis"],
                                "length": round(sep, 6),
                                "face_count_main": a["face_count"],
                            },
                            "face_count": a["face_count"] + b["face_count"],
                        }
                    )
    return slots


# 兼容旧入口
def detect_holes(cylinders: list[dict[str, Any]], radius_tol: float = 1e-3) -> list[dict[str, Any]]:
    cluster = _cluster_cylinders(cylinders)
    out = []
    for c in cluster:
        subtype, conf = _classify(c)
        out.append(
            {
                "type": subtype,
                "confidence": round(conf, 2),
                "radius": c["radius"],
                "axis": c["axis"],
                "location": c["location"],
                "depth": c["depth"],
                "cylindrical_face_count": c["face_count"],
            }
        )
    out.sort(key=lambda f: -f["radius"])
    return out
