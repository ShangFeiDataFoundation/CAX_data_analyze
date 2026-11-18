"""CAE 解析主流程：Fluent ``.cas.h5`` + ``.dat.h5`` -> 统一 JSON（校验 + 溯源）。"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
from typing import Any

import numpy as np

from . import fields as F
from .cff import FluentCase, FluentData
from .dataset import CaseEntry, CaeDataset
from .mesh import build_mesh_summary
from .multimodal import describe
from .render import render_all
from .setup import extract_setup
from cax_core.assets import render_assets
from cax_core.classification import ensure_classification
from cax_core.perf import span
from cax_core.schema import SCHEMA_VERSION

try:
    from .schema import dump_cae

    _SCHEMA_OK = True
except Exception:  # pragma: no cover
    _SCHEMA_OK = False

SCHEMA_VERSION_CAE = SCHEMA_VERSION
PARSER_NAME = "cae_pipeline"
PARSER_VERSION = "0.2 (Fluent CFF + h5py)"
PROVENANCE_STEPS = [
    "dataset_index",
    "open_case_stream",
    "read_mesh",
    "read_setup",
    "read_results",
    "derive_metrics",
    "render_views",
    "visual_describe",
    "schema_validate",
]

# 面场里逐 zone 统计的字段（其余场只做全局统计）
ZONE_FIELD_WHITELIST = {"SV_P", "SV_WALL_SHEAR", "SV_FLUX", "SV_VOF", "SV_WALL_DIFFUSIVE_BC_MFLUX",
                        "SV_T", "SV_K", "SV_OMEGA"}


def slugify(key: str) -> str:
    return key.replace("/", "__").replace("\\", "__").replace(" ", "_")


def _sha256_stream(dataset: CaeDataset, member: str, limit: int = 512 << 20) -> dict[str, Any]:
    size = dataset.member_size(member)
    if size > limit:
        return {"algorithm": "sha256", "skipped": True, "reason": f"entry larger than {limit >> 20} MiB",
                "bytes": size}
    h = hashlib.sha256()
    if dataset.is_zip:
        with dataset._zip.open(member) as fh:  # type: ignore[union-attr]
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
    else:
        with open(os.path.join(dataset.source, member.replace("/", os.sep)), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
    return {"algorithm": "sha256", "value": h.hexdigest(), "bytes": size}


def _phase_materials(setup: dict[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for p in setup.get("phases", []):
        if p.get("id") is not None and p.get("material"):
            out[int(p["id"])] = str(p["material"])
    return out


def _add_droplet_reference(derived: dict[str, Any], setup: dict[str, Any]) -> None:
    """用 case 里的初始液滴球（cell-registers）补出初始直径与铺展系数 β=D/D0。"""
    droplet = derived.get("droplet")
    if not droplet:
        return
    registers = (setup.get("initial_conditions") or {}).get("cell_registers") or []
    r0 = None
    for reg in registers:
        if (isinstance(reg, dict) and reg.get("type") == "sphere"
                and isinstance(reg.get("radius"), (int, float))):
            r0 = float(reg["radius"])
            break
    if not r0:
        droplet["spreading_ratio"] = None
        droplet["spreading_ratio_note"] = "case 中未找到球状初始液滴（cell-registers），无法给出 D0"
        return
    d0 = 2.0 * r0
    droplet["initial_drop_diameter_m"] = d0
    droplet["surface_type"] = (setup.get("surface") or {}).get("type")
    if droplet.get("film_contaminated"):
        # 湿表面：液膜与液滴同相，VOF 无法分离，只报告水相总足迹
        droplet["water_footprint_diameter_m"] = droplet.get("diameter_m")
        droplet["spreading_ratio"] = None
        droplet["spreading_ratio_note"] = (
            "湿表面算例：预置液膜与液滴为同一相，VOF 无法分离；"
            "diameter/height/等效球半径为「液膜+液滴」水相总量，铺展系数不给"
        )
        return
    drop_d = droplet.get("diameter_m") or 0.0
    droplet["spreading_ratio"] = round(drop_d / d0, 4) if d0 else None


def _clean(obj: Any) -> Any:
    """把 NaN/Inf/numpy 标量转成 JSON 安全的 Python 值。"""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [_clean(v) for v in obj.tolist()]
    return obj


def build_cae_json(dataset: CaeDataset, case_ref: str | CaseEntry, out_dir: str | None = None,
                   with_render: bool = True, with_caption: bool = True,
                   hash_members: bool = True, data_index: int = 0,
                   max_fields: int | None = None, captioner: Any = None) -> dict[str, Any]:
    """解析单个算例，返回统一信封 JSON 字典。"""
    entry = dataset.get_case(case_ref) if isinstance(case_ref, str) else case_ref
    warnings: list[str] = []
    if entry.cas is None:
        raise FileNotFoundError(f"算例 {entry.key} 没有 .cas.h5")
    if not entry.dats:
        warnings.append("该算例没有 .dat.h5，仅解析网格与设置（无结果场）")

    dat_member = entry.dats[min(data_index, len(entry.dats) - 1)] if entry.dats else None
    prefix = slugify(entry.key)

    with dataset.open_entry(entry.cas) as fcase, (
        dataset.open_entry(dat_member) if dat_member else _null_context()
    ) as fdata:
        fluent_case = FluentCase(fcase)
        data = FluentData(fdata) if fdata is not None else None

        with span("cae.read_mesh") as mesh_span:
            mesh_summary = build_mesh_summary(fluent_case)
            mesh_span.set_outputs(
                nodes=mesh_summary.data.get("nodes"),
                faces=mesh_summary.data.get("faces"),
                cells=mesh_summary.data.get("cells"),
            )
        with span("cae.read_setup") as setup_span:
            setup = extract_setup(fluent_case, data)
            setup_span.set_outputs(
                phases=len(setup.get("phases") or []),
                materials=len(setup.get("materials") or {}),
            )
        mat = _phase_materials(setup)
        geom = mesh_summary.cell_geometry
        if geom.negative_volume_cells:
            warnings.append(f"{geom.negative_volume_cells} 个单元体积非正，质量指标可能失真")

        results: list[dict[str, Any]] = []
        zone_results: list[dict[str, Any]] = []
        derived: dict[str, Any] = {}
        field_catalog: dict[str, Any] = {}
        viz_arrays: dict[str, np.ndarray] = {}
        vof_water = None

        if data is not None:
            with span("cae.read_field_catalog") as catalog_span:
                field_catalog = F.build_field_catalog(data)
                catalog_span.set_outputs(
                    phases=len(field_catalog.get("available") or {}),
                )
            totals = {"cells": fluent_case.cell_count, "faces": fluent_case.face_count}
            for phase in data.phases():
                for location in ("cells", "faces"):
                    names = data.field_names(location, phase)
                    if max_fields is not None:
                        names = names[:max_fields]
                    for name in names:
                        try:
                            results.append(F.summarize_field(data, name, location, phase, totals[location]))
                        except Exception as exc:
                            warnings.append(f"场 {name} ({location}, phase-{phase}) 读取失败：{exc}")
            for zone in fluent_case.face_zones():
                for name in ("SV_P", "SV_WALL_SHEAR", "SV_VOF", "SV_FLUX", "SV_WALL_DIFFUSIVE_BC_MFLUX"):
                    if name not in ZONE_FIELD_WHITELIST:
                        continue
                    for phase in data.phases():
                        if name not in data.field_names("faces", phase):
                            continue
                        try:
                            values = data.read_field(name, "faces", phase, total=fluent_case.face_count)
                        except Exception:
                            continue
                        row = F.summarize_zone_field(values, zone)
                        row["field"] = name
                        row["phase"] = phase
                        row["unit"] = F.field_meta(name)["unit"]
                        stats = row["stats"]
                        if isinstance(stats, dict) and stats.get("count", 0) == 0:
                            continue  # 该 zone 未存盘
                        zone_results.append(row)
            film_info = (setup.get("surface") or {}).get("pre_existing_film")
            film_top_z = film_info.get("film_top_z_m") if film_info else None
            with span("cae.derive_metrics") as derived_span:
                derived = F.derived_fields(
                    data,
                    fluent_case,
                    geom,
                    mat,
                    film_top_z=film_top_z,
                )
                derived_span.set_outputs(metrics=len(derived))
            if derived.get("droplet") is not None:
                derived["droplet"]["film_contaminated"] = bool(film_info)
            _add_droplet_reference(derived, setup)

            # 出图用数组：优先水相 VOF，避免同时画互补的两个相
            water_phase_ids = [wp for wp, m in mat.items() if m and "water" in m.lower()]
            for phase in data.phases():
                if "SV_VOF" not in data.field_names("cells", phase):
                    continue
                vof = F.sanitize(data.read_field("SV_VOF", "cells", phase,
                                                 total=fluent_case.cell_count))[0]
                if phase in water_phase_ids:
                    vof_water = vof
                    viz_arrays[f"VOF phase-{phase} ({mat.get(phase)})"] = vof
            if vof_water is None:  # 未识别到水相材料时退回第一个可用相
                for phase in data.phases():
                    if "SV_VOF" in data.field_names("cells", phase):
                        vof_water = F.sanitize(data.read_field("SV_VOF", "cells", phase,
                                                              total=fluent_case.cell_count))[0]
                        viz_arrays[f"VOF phase-{phase}"] = vof_water
                        break
            speed = F.velocity_magnitude(data, "cells", 1, fluent_case.cell_count)
            if speed.size:
                viz_arrays["|U| phase-1 [m/s]"] = speed
            if "SV_P" in data.field_names("cells", 1):
                viz_arrays["p phase-1 [Pa]"] = data.read_field("SV_P", "cells", 1, total=fluent_case.cell_count)

    rendered: list[dict[str, Any]] = []
    if with_render:
        viz_dir = os.path.join(out_dir or ".", "viz")
        with span("cae.render_views") as render_span:
            try:
                with dataset.open_entry(entry.cas) as fcase:
                    fluent_case2 = FluentCase(fcase)
                    rendered = render_all(
                        fluent_case2,
                        viz_dir,
                        prefix,
                        centroids=mesh_summary.cell_geometry.centroids,
                        field_arrays=viz_arrays,
                        vof_water=vof_water,
                        title=f"{entry.key}",
                    )
                    render_span.set_outputs(views=len(rendered))
            except Exception as exc:
                render_span.set_status("warning", error=str(exc))
                warnings.append(f"渲染失败：{exc}")

    content: dict[str, Any] = {
        "analysis": {
            "physics": "multiphase_flow" if setup["models"]["multiphase"]["enabled"] else "fluid_flow",
            "solver": f"ANSYS Fluent {setup['solver']['version']}",
            "models": setup["models"],
            "phases": setup["phases"],
            "time": setup["time"],
            "units": {**setup["units"], "operating_units": setup["operating_units"]},
            "operating_conditions": setup["operating_conditions"],
        },
        "mesh": mesh_summary.data,
        "setup": {
            "materials": setup["materials"],
            "boundary_conditions": setup["boundary_conditions"],
            "cell_zones": setup["cell_zones"],
            "initial_conditions": setup["initial_conditions"],
            "surface": setup["surface"],
        },
        "field_catalog": field_catalog,
        "results": results,
        "zone_results": zone_results,
        "derived": derived,
        "rendered_views": render_assets(rendered, out_dir),
    }
    if with_caption:
        content["visual"] = describe(content, rendered, captioner=captioner)
    else:
        content["visual"] = None

    if hash_members:
        with span(
            "source.sha256",
            inputs={
                "files": 1 + int(bool(dat_member)),
                "case_bytes": entry.cas_bytes,
                "data_bytes": entry.dat_bytes,
            },
        ) as hash_span:
            case_hash = _sha256_stream(dataset, entry.cas)
            data_hash = _sha256_stream(dataset, dat_member) if dat_member else {}
            hash_span.set_outputs(
                case_hash_chars=len(case_hash.get("value") or ""),
                data_hash_chars=len(data_hash.get("value") or ""),
            )
    else:
        case_hash = {}
        data_hash = {}
    def _source_path(member: str) -> str:
        if dataset.is_zip:
            return f"{dataset.source}::{member}"
        return os.path.join(dataset.source, member.replace("/", os.sep))

    source_files: list[dict[str, Any]] = [{
        "role": "case",
        "path": _source_path(entry.cas),
        "format": "FLUENT_CFF",
        "bytes": entry.cas_bytes,
        "sha256": case_hash.get("value"),
        "hash_skipped": bool(case_hash.get("skipped") or not hash_members),
        "hash_skip_reason": case_hash.get("reason") or (None if hash_members else "hash disabled"),
    }]
    if dat_member:
        source_files.append({
            "role": "data",
            "path": _source_path(dat_member),
            "format": "FLUENT_CFF",
            "bytes": dataset.member_size(dat_member),
            "sha256": data_hash.get("value"),
            "hash_skipped": bool(data_hash.get("skipped") or not hash_members),
            "hash_skip_reason": data_hash.get("reason") or (None if hash_members else "hash disabled"),
        })
    source: dict[str, Any] = {
        "primary_format": "FLUENT_CFF",
        "files": source_files,
        "parser": {"name": PARSER_NAME, "version": PARSER_VERSION},
        "parsed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "solver": f"ANSYS Fluent {setup['solver']['version']}",
        "dataset": {
            "source": dataset.source,
            "entry": entry.key,
            "access": dataset.access,
            "case_file": entry.cas,
            "data_files": list(entry.dats),
            "case_bytes": entry.cas_bytes,
            "data_bytes": entry.dat_bytes,
        },
    }
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_CAE,
        "data_type": "cae",
        "source": source,
        "meta": {
            "validation": {"is_valid": True, "warnings": warnings, "errors": []},
            "provenance": {
                "source_files": [item["path"] for item in source_files],
                "steps": PROVENANCE_STEPS,
            },
            "capabilities": {
                "geometry": bool(mesh_summary.data.get("nodes")),
                "mesh": True,
                "setup": bool(content.get("setup")),
                "results": bool(results),
                "render": bool(rendered),
                "vision": bool(content.get("visual")),
            },
            "tags": [entry.group.lower() if entry.group else "cae", "fluent", "cfd"],
            "domain": "commercial_aircraft",
        },
        "content": content,
    }
    doc = _clean(doc)
    with span("classification.build") as classification_span:
        ensure_classification(doc)
        classification_span.set_outputs(
            facets=len((doc["meta"]["classification"] or {}).get("facets") or []),
            categories=len((doc["meta"]["classification"] or {}).get("summary") or []),
        )
    if _SCHEMA_OK:
        with span("schema.validate") as schema_span:
            try:
                doc = dump_cae(doc)
                schema_span.set_outputs(valid=True)
            except Exception as exc:
                schema_span.set_status("error", error=str(exc))
                doc["meta"]["validation"] = {
                    "is_valid": False,
                    "warnings": warnings,
                    "errors": [f"schema_error: {exc}"],
                }
    return doc


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc) -> None:
        return None


def parse_cae_case(dataset: CaeDataset | str, case: str | CaseEntry,
                   out_dir: str | None = None, with_render: bool = True,
                   with_caption: bool = True, write_json: bool = True,
                   hash_members: bool = True, captioner: Any = None,
                   **kwargs: Any) -> dict[str, Any]:
    """便捷入口：解析（可选下载入/写出 JSON）。dataset 可传路径或 CaeDataset。"""
    own = isinstance(dataset, str)
    ds = CaeDataset(dataset) if own else dataset
    try:
        doc = build_cae_json(ds, case, out_dir=out_dir, with_render=with_render,
                             with_caption=with_caption, hash_members=hash_members,
                             captioner=captioner, **kwargs)
        if write_json:
            entry = ds.get_case(case) if isinstance(case, str) else case
            out_dir = out_dir or "json_out"
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"{slugify(entry.key)}_cae.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
            doc["_written_to"] = path
        return doc
    finally:
        if own:
            ds.close()
