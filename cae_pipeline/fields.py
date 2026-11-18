"""结果场提取：字段目录、统计量、派生量（速度幅值/液滴形态/润湿面积）。"""

from __future__ import annotations

from typing import Any

import numpy as np

from .cff import FluentCase, FluentData, Zone
from .mesh import CellGeometry

# SV_ 场名 -> (语义名, 单位, 说明)
FIELD_META: dict[str, tuple[str, str | None, str]] = {
    "SV_P": ("pressure", "Pa", "静压（表压）"),
    "SV_P_M1": ("pressure_previous", "Pa", "上一时间步静压"),
    "SV_U": ("velocity", "m/s", "速度 x 分量"),
    "SV_V": ("velocity", "m/s", "速度 y 分量"),
    "SV_W": ("velocity", "m/s", "速度 z 分量"),
    "SV_U_M1": ("velocity_previous", "m/s", "上一时间步速度 x 分量"),
    "SV_V_M1": ("velocity_previous", "m/s", "上一时间步速度 y 分量"),
    "SV_W_M1": ("velocity_previous", "m/s", "上一时间步速度 z 分量"),
    "SV_VOF": ("volume_fraction", "1", "相体积分数（VOF）"),
    "SV_VOF_M1": ("volume_fraction_previous", "1", "上一时间步相体积分数"),
    "SV_DENSITY": ("density", "kg/m^3", "密度"),
    "SV_DENSITY_M1": ("density_previous", "kg/m^3", "上一时间步密度"),
    "SV_MU_LAM": ("viscosity", "kg/(m*s)", "层流粘度"),
    "SV_T": ("temperature", "K", "温度"),
    "SV_K": ("turbulent_kinetic_energy", "m^2/s^2", "湍动能 k"),
    "SV_EPSILON": ("turbulent_dissipation", "m^2/s^3", "湍流耗散率"),
    "SV_OMEGA": ("specific_dissipation", "1/s", "比耗散率 omega"),
    "SV_NUT": ("turbulent_viscosity", "kg/(m*s)", "湍流粘度"),
    "SV_BF_V": ("body_force", "N/m^3", "体积力矢量"),
    "SV_FLUX": ("mass_flux", "kg/s", "面质量流量"),
    "SV_VFLUX": ("volume_flux", "m^3/s", "面体积流量"),
    "SV_WALL_V": ("wall_velocity", "m/s", "壁面速度矢量"),
    "SV_WALL_SHEAR": ("wall_shear_stress", "Pa", "壁面剪应力矢量"),
    "SV_WALL_DIFFUSIVE_BC_MFLUX": ("wall_diffusive_mass_flux", "kg/(m^2*s)", "壁面扩散质量通量"),
}

COMPONENT_NAMES = {3: ["x", "y", "z"]}

# Fluent CFF 用 ±DBL_MAX 表示「未定义/未初始化」的场值（如初始时刻的上一时间步量）
UNDEFINED_ABS_LIMIT = 1e300


def sanitize(values: np.ndarray) -> tuple[np.ndarray, int]:
    """把 ±DBL_MAX / 非有限值统一记为 NaN，返回 (清洗后的数组, 未定义值个数)。"""
    arr = np.asarray(values, dtype=np.float64)
    bad = ~np.isfinite(arr)
    if arr.size:
        bad |= np.abs(np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)) >= UNDEFINED_ABS_LIMIT
    count = int(bad.sum())
    if count:
        arr = arr.copy()
        arr[bad] = np.nan
    return arr, count


def field_meta(name: str) -> dict[str, Any]:
    semantic, unit, note = FIELD_META.get(name, (name, None, "未登记的场"))
    return {"field": name, "semantic": semantic, "unit": unit, "note": note}


def _stats(values: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    if arr.size > 2_000_000:
        step = arr.size // 2_000_000 + 1
        arr = arr[::step]
    qs = np.percentile(arr, [1, 25, 50, 75, 99]) if arr.size > 100 else np.percentile(arr, [0, 25, 50, 75, 100])
    return {
        "count": int(arr.size),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "p1": float(qs[0]),
        "p25": float(qs[1]),
        "p50": float(qs[2]),
        "p75": float(qs[3]),
        "p99": float(qs[4]),
    }


def _vector_stats(values: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=np.float64)
    out: dict[str, Any] = {"magnitude": _stats(np.linalg.norm(arr, axis=1))}
    for i, comp in enumerate(COMPONENT_NAMES.get(arr.shape[1], [str(i) for i in range(arr.shape[1])])):
        out[comp] = _stats(arr[:, i])
    return out


def summarize_field(data: FluentData, name: str, location: str, phase: int,
                    total: int) -> dict[str, Any]:
    values, undefined = sanitize(data.read_field(name, location, phase, total=total))
    meta = field_meta(name)
    sections = data.field_sections(name, location, phase)
    covered = int(sum(n for _lo, _hi, n, _w in sections))
    out = {
        **meta,
        "phase": phase,
        "location": location,
        "coverage": round(covered / max(total, 1), 4),
        "defined_values": int(np.isfinite(values).sum()),
        "undefined_values": undefined,
        "sections": [{"min_id": lo, "max_id": hi, "count": n} for lo, hi, n, _w in sections],
    }
    if values.ndim == 2 and values.shape[1] > 1:
        out["components"] = COMPONENT_NAMES.get(values.shape[1], [str(i) for i in range(values.shape[1])])
        out["stats"] = _vector_stats(values)
    else:
        out["stats"] = _stats(values)
    return out


def summarize_zone_field(values: np.ndarray, zone: Zone) -> dict[str, Any]:
    lo, hi = zone.min_id - 1, zone.max_id
    sub = sanitize(values[lo:hi])[0]
    stats = _vector_stats(sub) if (sub.ndim == 2 and sub.shape[1] > 1) else _stats(sub)
    return {"zone": zone.name, "type": zone.type_label, "faces": zone.count, "stats": stats}


def velocity_magnitude(data: FluentData, location: str, phase: int, total: int) -> np.ndarray:
    comps = []
    for key in ("SV_U", "SV_V", "SV_W"):
        try:
            comps.append(sanitize(data.read_field(key, location, phase, total=total))[0])
        except KeyError:
            return np.array([])
    return np.sqrt(sum(c * c for c in comps))


def derived_fields(data: FluentData, case: FluentCase, geom: CellGeometry,
                   phase_materials: dict[int, str],
                   film_top_z: float | None = None) -> dict[str, Any]:
    """派生量：速度幅值统计、相分布统计、液滴形态与润湿面积。"""
    out: dict[str, Any] = {}
    ncells, nfaces = case.cell_count, case.face_count

    speeds = []
    for phase in data.phases():
        mag = velocity_magnitude(data, "cells", phase, ncells)
        if mag.size:
            item = {"phase": phase, "stats": _stats(mag)}
            speeds.append(item)
    if speeds:
        out["velocity_magnitude"] = speeds

    # 相分布：VOF
    water_phases = [p for p, m in phase_materials.items() if m and "water" in str(m).lower()]
    phase_distribution = []
    for phase in data.phases():
        if "SV_VOF" not in data.field_names("cells", phase):
            continue
        vof = sanitize(data.read_field("SV_VOF", "cells", phase, total=ncells))[0]
        finite = np.isfinite(vof)
        item = {
            "phase": phase,
            "material": phase_materials.get(phase),
            "stats": _stats(vof),
            "interface_cells": int(np.count_nonzero(finite & (vof > 0.05) & (vof < 0.95))),
            "volume": float(np.nansum(vof * geom.volumes)),
        }
        phase_distribution.append(item)
    if phase_distribution:
        out["phase_distribution"] = phase_distribution

    # 液滴形态（水相 VOF>0.5 的单元）
    if water_phases:
        wp = water_phases[0]
        vof = sanitize(data.read_field("SV_VOF", "cells", wp, total=ncells))[0]
        cen = geom.centroids
        mask = np.isfinite(vof) & (vof > 0.5)
        valid = mask & np.isfinite(cen).all(axis=1)
        if valid.any():
            pts = cen[valid]
            vol = float(np.nansum(geom.volumes[valid]))
            centroid = pts.mean(axis=0)
            zmin = float(geom.centroids[:, 2].min()) if np.isfinite(geom.centroids[:, 2]).any() else float(pts[:, 2].min())
            radial = np.hypot(pts[:, 0] - centroid[0], pts[:, 1] - centroid[1])
            height = pts[:, 2] - zmin
            r_eq = (3.0 * vol / (4.0 * np.pi)) ** (1.0 / 3.0) if vol > 0 else 0.0
            metrics: dict[str, Any] = {
                "phase": wp,
                "material": phase_materials.get(wp),
                "criterion": "cell VOF > 0.5",
                "metric_scope": ("water_phase_film_and_droplet" if film_top_z is not None
                                 else "droplet"),
                "film_top_z_m": film_top_z,
                "cells": int(valid.sum()),
                "volume_m3": vol,
                "centroid": [float(x) for x in centroid],
                "bbox": {
                    "xmin": float(pts[:, 0].min()), "xmax": float(pts[:, 0].max()),
                    "ymin": float(pts[:, 1].min()), "ymax": float(pts[:, 1].max()),
                    "zmin": float(pts[:, 2].min()), "zmax": float(pts[:, 2].max()),
                },
                "diameter_m": float(2 * radial.max()),
                "height_m": float(height.max()),
                "equivalent_sphere_radius_m": float(r_eq),
            }
            metrics["spreading_ratio"] = None
            metrics["splat_note"] = "diameter/height 由 VOF>0.5 单元重心估计，非界面重构精确值"
            metrics["film_contaminated"] = None
            out["droplet"] = metrics

    # 润湿面积：壁面 zone 中相邻单元 VOF>0.5 的面面积之和
    wall = next((z for z in case.face_zones() if z.type_label == "wall"), None)
    if wall is not None and water_phases:
        vof = sanitize(data.read_field("SV_VOF", "cells", water_phases[0], total=ncells))[0]
        c0 = case.face_cells(0)[wall.min_id - 1: wall.max_id]
        areas = geom.face_areas[wall.min_id - 1: wall.max_id]
        ok = (c0 > 0) & (c0 <= ncells)
        wet = np.zeros_like(ok)
        cell_idx = c0[ok] - 1
        wet[ok] = np.nan_to_num(vof[cell_idx], nan=0.0) > 0.5
        out["wetted_area"] = {
            "zone": wall.name,
            "criterion": "相邻单元水相 VOF > 0.5",
            "wetted_faces": int(wet.sum()),
            "total_faces": int(wall.count),
            "wetted_area_m2": float(areas[wet].sum()),
            "zone_area_m2": float(areas.sum()),
        }
    return out


def build_field_catalog(data: FluentData) -> dict[str, Any]:
    """列出可用场（含语义与单位），并标注未保存的常见量。"""
    catalog = data.field_catalog()
    described: dict[str, Any] = {}
    for phase_key, locations in catalog.items():
        described[phase_key] = {}
        for loc, names in locations.items():
            described[phase_key][loc] = [field_meta(n) for n in names]
    expected = ["SV_T", "SV_K", "SV_OMEGA", "SV_EPSILON", "SV_NUT"]
    present = {n for locs in catalog.values() for names in locs.values() for n in names}
    return {
        "available": described,
        "not_saved_in_this_file": [field_meta(n) for n in expected if n not in present],
    }
