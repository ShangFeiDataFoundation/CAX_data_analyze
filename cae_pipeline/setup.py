"""从 ``.cas.h5`` / ``.dat.h5`` 的设置段提取求解设定（材料、模型、边界条件、时间步）。"""

from __future__ import annotations

from typing import Any, Iterator

from . import scheme as S
from .cff import FluentCase, FluentData

# 湍流模型：Rampant Variables 中的互斥开关
TURBULENCE_FLAGS = [
    ("rp-inviscid?", "inviscid (Euler)"),
    ("rp-lam?", "laminar"),
    ("rp-sa?", "Spalart-Allmaras"),
    ("rp-ke?", "k-epsilon"),
    ("rp-kw?", "k-omega"),
    ("rp-kklw?", "k-kl-omega (transition)"),
    ("rp-les?", "LES"),
    ("rp-des?", "DES"),
    ("rp-sas?", "SAS"),
    ("rp-rsm?", "Reynolds stress"),
]

SOLVER_MODE_FLAGS = [
    ("sg-mphase?", "multiphase"),
    ("sg-dpm?", "discrete phase (DPM)"),
    ("sg-species?", "species transport"),
    ("sg-pdf?", "PDF combustion"),
    ("sg-premixed?", "premixed combustion"),
    ("sg-melt?", "melting/solidification"),
    ("sg-solar?", "solar load"),
    ("sg-wallfilm?", "wall film"),
]


def _records(node: Any, kind: str) -> Iterator[tuple[list, dict[str, Any]]]:
    """找形如 ``((3 phase-domain voda) (material . water-liquid) ...)`` 的记录。"""
    for sub in S.walk(node):
        if not isinstance(sub, list) or not sub:
            continue
        head = sub[0]
        if isinstance(head, list) and len(head) >= 3 and str(head[1]) == kind:
            yield head, S.sexp_to_dict(sub[1:])


def _num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _const_value(value: Any) -> float | None:
    """把 ``(constant . 0.075)`` -> 0.075；``(profile "f" "")`` 返回 None。"""
    if isinstance(value, dict):
        return _num(value["constant"]) if "constant" in value else None
    if isinstance(value, list):
        if len(value) >= 3 and isinstance(value[1], S.Sym) and str(value[1]) == ".":
            return _num(value[2]) if str(value[0]) == "constant" else None
        if len(value) == 2:
            return _num(value[1]) if str(value[0]) == "constant" else None
        return None
    return _num(value)


def extract_setup(case: FluentCase, data: FluentData | None = None) -> dict[str, Any]:
    rampant_text = case.settings_text("Rampant Variables") or ""
    thread_text = case.settings_text("Thread Variables") or ""
    cortex_text = case.settings_text("Cortex Variables") or ""
    domain_text = case.settings_text("Domain Variables") or ""

    rampant = S.parse(rampant_text)
    cortex = S.parse(cortex_text)
    domain = S.parse(domain_text)

    # --- 相与材料 -----------------------------------------------------
    phases: list[dict[str, Any]] = []
    for head, body in _records(rampant, "phase-domain"):
        phases.append(
            {
                "id": int(head[0]) if isinstance(head[0], (int, float)) else None,
                "name": str(head[2]),
                "material": body.get("material"),
                "parent_id": body.get("parent-id"),
            }
        )
    phase_by_id = {p["id"]: p for p in phases}
    # Fluent VOF 的相列表里，父相（mixture，通常 id=1）不出现在 phase-domain 记录中
    parent_ids = sorted({p["parent_id"] for p in phases if isinstance(p.get("parent_id"), int)})
    for pid in parent_ids:
        if pid in phase_by_id:
            continue
        mixture = {"id": pid, "name": "mixture", "material": None, "parent_id": None,
                   "role": "mixture (父相)"}
        phases.insert(0, mixture)
        phase_by_id[pid] = mixture
    for p in phases:
        p.setdefault("role", "phase")

    # --- 模型 ---------------------------------------------------------
    turbulence = "unknown"
    active_flags = []
    for key, label in TURBULENCE_FLAGS:
        value = S.find_key(rampant, key)
        if value is True:
            turbulence = label
            active_flags.append(key)
    if turbulence == "k-omega":
        # 区分标准 k-omega 与 SST：SST 打开时会有 sst 相关选项生效
        turbulence = "k-omega SST" if S.find_key(rampant, "rp-trans-sst?") is not None else "k-omega"

    gravity = [
        _num(S.find_key(rampant, f"gravity/{axis}-expr"), 0.0) for axis in ("x", "y", "z")
    ]
    energy = S.find_key(rampant, "rf-energy?")
    multiphase = {
        "enabled": len(phases) > 1,
        "model": None,
        "phase_count": len(phases),
        "evidence": [],
    }
    if len(phases) > 1:
        multiphase["model"] = "VOF"
    if data is not None:
        vof_fields = [f for p in data.phases() for f in data.field_names("cells", p) if "VOF" in f]
        if vof_fields:
            multiphase["model"] = "VOF"
            multiphase["evidence"].append("结果文件中存在 SV_VOF 场")

    surface_tension = None
    st = S.find_key(rampant, "sfc-tension-coeff")
    if isinstance(st, list) and len(st) >= 3:
        value = _const_value(st[-1])
        if value is not None:
            surface_tension = {"pair": [str(st[0]), str(st[1])], "value": value, "unit": "N/m"}

    solver_flags = {
        label: bool(S.find_key(rampant, key))
        for key, label in SOLVER_MODE_FLAGS
        if S.find_key(rampant, key) is not None
    }

    # --- 时间 ---------------------------------------------------------
    dt = _num(S.find_key(rampant, "physical-time-step-expr"))
    total_steps = _num(S.find_key(domain, "time/total-timesteps"))
    if total_steps is None and data is not None and data.data_variables:
        total_steps = _num(S.find_key(S.parse(data.data_variables), "time/total-timesteps"))
    if dt is None and data is not None and data.data_variables:
        dt = _num(S.find_key(S.parse(data.data_variables), "physical-time-step"))
    flow_time = data.flow_time if data is not None else None
    time_step_index = data.time_step if data is not None else None
    unsteady = bool(total_steps) or (flow_time is not None)

    # --- 单位 ---------------------------------------------------------
    unit_table = S.find_key(cortex, "unit-table")
    display_units: dict[str, Any] = {}
    if isinstance(unit_table, list):
        for item in unit_table:
            if isinstance(item, list) and len(item) >= 3:
                display_units[str(item[0])] = {"unit": str(item[1]), "scale": _num(item[2])}

    # --- 边界条件 -----------------------------------------------------
    thread_blocks = S.thread_blocks(thread_text)
    blocks_by_zone: dict[str, list[dict[str, Any]]] = {}
    for b in thread_blocks:
        blocks_by_zone.setdefault(str(b["name"]), []).append(b)

    bc_rows: list[dict[str, Any]] = []
    for zone in case.face_zones():
        blocks = blocks_by_zone.get(zone.name, [])
        kind = blocks[0]["kind"] if blocks else zone.type_label
        row: dict[str, Any] = {
            "name": zone.name,
            "type": kind,
            "type_code": int(zone.zone_type),
            "zone_id": int(zone.zone_id),
            "faces": zone.count,
            "adjacent_cell_zone": int(zone.c0) if zone.c0 else None,
            "opposite_cell_zone": int(zone.c1) if zone.c1 is not None else None,
            "is_boundary": zone.is_boundary,
            "per_phase": [],
        }
        for b in sorted(blocks, key=lambda x: str(x["phase"])):
            st_settings = b["settings"]
            phase_id = int(b["phase"]) if isinstance(b["phase"], (int, float)) else None
            item: dict[str, Any] = {
                "phase": phase_id,
                "phase_name": (phase_by_id.get(phase_id) or {}).get("name"),
                "material": st_settings.get("material"),
            }
            for key, out_key in (("vmag", "velocity_magnitude"), ("u", "velocity_x"),
                                 ("v", "velocity_y"), ("w", "velocity_z"),
                                 ("t", "temperature"), ("roughness-height", "roughness_height")):
                value = _const_value(st_settings.get(key))
                if value is not None:
                    item[out_key] = value
            row["per_phase"].append(item)
        bc_rows.append(row)

    cell_zones: list[dict[str, Any]] = []
    for zone in case.cell_zones():
        blocks = blocks_by_zone.get(zone.name, [])
        entry: dict[str, Any] = {
            "name": zone.name,
            "type": zone.type_label,
            "zone_id": int(zone.zone_id),
            "cells": zone.count,
            "materials": sorted({str(b["settings"].get("material")) for b in blocks
                                 if b["settings"].get("material")}),
            "per_phase_material": [
                {
                    "phase": int(b["phase"]) if isinstance(b["phase"], (int, float)) else None,
                    "phase_name": (phase_by_id.get(int(b["phase"]) if isinstance(b["phase"], (int, float)) else -1) or {}).get("name"),
                    "material": b["settings"].get("material"),
                }
                for b in sorted(blocks, key=lambda x: str(x["phase"]))
            ],
        }
        cell_zones.append(entry)

    # --- 初始条件（cell register，如初始液滴球体） ---------------------
    registers: list[dict[str, Any]] = []
    reg_node = S.find_key(rampant, "cell-registers")
    if isinstance(reg_node, list):
        for item in reg_node:
            body = S.sexp_to_dict(item)
            if not body:
                continue
            entry: dict[str, Any] = {"name": body.get("name")}
            type_raw = body.get("type")
            if isinstance(type_raw, list) and type_raw:
                entry["type"] = str(type_raw[0])
                params = S.sexp_to_dict([p for p in type_raw[1:] if isinstance(p, list)])
                for key, value in params.items():
                    entry[key.rstrip("?")] = value
            else:
                entry["type"] = str(type_raw)
            for key in ("center", "radius", "min-point", "max-point"):
                if key in body and key not in entry:
                    entry[key] = body[key]
            registers.append(entry)

    # 湿表面算例用六面体 register 描述预置液膜（其顶面 = 液膜高度）
    film = next((r for r in registers if r.get("type") == "hexahedron"), None)
    film_top_z = None
    if film and isinstance(film.get("max-point"), list) and len(film["max-point"]) == 3:
        film_top_z = _num(film["max-point"][2])

    material_names = sorted(
        {str(p["material"]) for p in phases if p.get("material")}
        | {str(m) for z in cell_zones for m in z["materials"]}
    )
    materials = [{"name": m, "role": _material_role(m, phases, cell_zones)} for m in material_names]

    setup: dict[str, Any] = {
        "solver": {
            "software": "ANSYS Fluent",
            "version": case.version,
            "origin": case.origin,
        },
        "models": {
            "turbulence": turbulence,
            "turbulence_flags": active_flags,
            "energy_equation": bool(energy),
            "multiphase": multiphase,
            "gravity": gravity,
            "surface_tension": surface_tension,
            "other_models": solver_flags,
        },
        "phases": phases,
        "materials": materials,
        "operating_conditions": {
            "pressure": _num(S.find_key(rampant, "operating-pressure-expr")) or
                        _num(S.find_key(domain, "operating-pressure")),
            "temperature": _num(S.find_key(rampant, "operating-temperature-expr")),
            "density": _num(S.find_key(rampant, "operating-density-expr")),
        },
        "time": {
            "transient": unsteady,
            "time_step_size_s": dt,
            "total_time_steps": int(total_steps) if total_steps else None,
            "flow_time_s": flow_time,
            "time_step_index": time_step_index,
            "case_file": data.case_file if data is not None else None,
        },
        "units": {
            "coordinate_unit": "m",
            "coordinate_unit_note": "Fluent CFF 中坐标与物理量以 SI 存储",
            "display_units": display_units,
        },
        "operating_units": {
            "length": "m", "velocity": "m/s", "pressure": "Pa",
            "temperature": "K", "density": "kg/m^3", "viscosity": "kg/(m*s)",
        },
        "boundary_conditions": bc_rows,
        "cell_zones": cell_zones,
        "initial_conditions": {"cell_registers": registers},
        "surface": {
            "type": "wet" if film else "dry",
            "pre_existing_film": (
                {"register": film.get("name"), "min_point": film.get("min-point"),
                 "max_point": film.get("max-point"), "film_top_z_m": film_top_z}
                if film else None
            ),
        },
    }
    return setup


def _material_role(name: str, phases: list[dict[str, Any]], cell_zones: list[dict[str, Any]]) -> str:
    for p in phases:
        if p.get("material") == name:
            return f"phase {p['id']} ({p['name']})"
    for z in cell_zones:
        if name in z["materials"]:
            return f"cell zone {z['name']}"
    return "unknown"
