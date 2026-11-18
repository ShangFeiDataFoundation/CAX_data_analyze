"""Nastran BDF（文本输入文件）解析。

不依赖 pyNastran：BDF 是固定列/自由格式的纯文本卡片流，
这里做"卡片统计 + 网格规模 + 求解类型 + 属性/材料/约束/气动卡片清单"。
支持 ``INCLUDE 'file.inc'`` 递归（相对被包含文件所在目录解析）。

结果侧（OP2 二进制）不在本模块范围。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
import os
import re
from typing import Any

from cax_core.schema import SCHEMA_VERSION

PARSER_NAME = "cae_pipeline/0.2 (Nastran BDF text)"

_INCLUDE_RE = re.compile(r"""^\s*INCLUDE\s+['"]?([^'"\s]+)['"]?\s*$""", re.I)

_ELEM_3D = {"CTETRA", "CHEXA", "CPENTA", "CPYRA"}
_ELEM_2D = {"CQUAD4", "CQUAD8", "CQUAD", "CTRIA3", "CTRIA6", "CTRIAR", "CSHEAR"}
_ELEM_1D = {"CBAR", "CBEAM", "CROD", "CONROD", "CTUBE", "CBEND"}
_PROPS = {"PSHELL", "PSOLID", "PBAR", "PBEAM", "PROD", "PCOMP", "PCOMPG", "PTUBE", "PELAS", "PBUSH"}
_MATS = {"MAT1", "MAT2", "MAT8", "MAT9"}
_CONSTRAINTS = {"SPC", "SPC1", "SPCADD", "MPC", "MPCADD"}
_LOADS = {"FORCE", "MOMENT", "PLOAD4", "PLOAD2", "GRAV", "TEMP", "TEMPD", "RFORCE"}
_AERO = {"CAERO1", "CAERO2", "CAERO3", "CAERO4", "CAERO5", "AERO", "MKAERO1", "MKAERO2",
         "FLFACT", "AESURF", "TRIM", "AEFACT", "PAERO1", "PAERO2", "SPLINE1", "SPLINE2",
         "SPLINE3", "SPLINE4", "SPLINE5"}
_MODES = {"EIGR", "EIGRL", "EIGB"}

# 单元 → 节点数（截取 connectivity，避免把 CBAR 的 X1/X2/X3 当成节点）
_ELEM_NNODES = {
    "CTETRA": 4, "CHEXA": 8, "CPENTA": 6, "CPYRA": 5,
    "CQUAD4": 4, "CQUAD8": 8, "CQUAD": 4, "CTRIA3": 3, "CTRIA6": 6, "CTRIAR": 3, "CSHEAR": 4,
    "CBAR": 2, "CBEAM": 2, "CROD": 2, "CONROD": 2, "CTUBE": 2, "CBEND": 2,
}

_SOL_PHYSICS = {
    101: "structural_static",
    103: "structural_modal",
    105: "structural_buckling",
    106: "structural_nonlinear_static",
    109: "structural_dynamic",
    111: "structural_modal_complex",
    112: "structural_dynamic",
    129: "structural_dynamic",
    144: "aeroelastic_flutter",
    145: "aeroelastic_flutter",
    146: "aeroelastic_response",
    153: "aeroelastic_dynamic",
}


def _sha256(path: str) -> dict[str, Any]:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"algorithm": "sha256", "value": h.hexdigest(), "bytes": os.path.getsize(path)}


def _split_fields(line: str) -> list[str]:
    """切分一行卡片：含逗号走自由格式，否则按 8 列固定格式。"""
    s = line.rstrip("\r\n")
    if "," in s[:12] or "\t" in s[:12] or s.lstrip().startswith(","):
        parts = re.split(r"[,\t]", s)
        name = parts[0].strip().rstrip("*").upper()
        return [name] + [f.strip() for f in parts[1:]]
    name = s[:8].strip().rstrip("*").upper()
    rest = [s[i:i + 8].strip() for i in range(8, len(s), 8)] if len(s) > 8 else []
    while rest and rest[-1] == "":
        rest.pop()
    return [name] + rest


def _continuation_fields(line: str) -> list[str]:
    """续行字段：制表符分隔 / '+' 标记 / 固定 8 列三种写法。"""
    s = line.rstrip("\r\n")
    if "\t" in s:
        return [f.strip() for f in s.split() if f.strip()]
    if s.lstrip().startswith("+"):
        body = s.lstrip()[1:]
        return [f.strip() for f in (body[i:i + 8] for i in range(0, len(body), 8)) if f.strip()]
    body = s[8:] if len(s) > 8 else ""
    return [f.strip() for f in (body[i:i + 8] for i in range(0, len(body), 8)) if f.strip()]


def _scan_case_control(path: str) -> tuple[int | None, int]:
    """扫描 BEGIN BULK 之前的执行/工况控制段：SOL 与 SUBCASE 数。"""
    sol = None
    subcases = 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                u = raw.strip().upper()
                if u.startswith("BEGIN BULK"):
                    break
                m = re.match(r"^SOL\s+(\d+)", u)
                if m:
                    sol = int(m.group(1))
                if u.startswith("SUBCASE"):
                    subcases += 1
    except OSError:
        pass
    return sol, subcases


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    # Nastran 简写：1.0-3 / 3.+7
    t = re.sub(r"(\d)([+-])(\d+)$", r"\1E\2\3", t)
    try:
        return float(t)
    except ValueError:
        return None


def _iter_cards(path: str, warnings: list[str], included: list[str], depth: int = 0, in_bulk: bool = False):
    """逐卡片产出 (NAME, fields)；自动展开 INCLUDE 并合并续行（仅 bulk 段）。"""
    if depth > 12:
        warnings.append(f"INCLUDE 嵌套过深，已停止：{path}")
        return
    base_dir = os.path.dirname(os.path.abspath(path))
    cur: list[str] | None = None

    def flush():
        nonlocal cur
        if cur:
            yield (cur[0].upper(), cur[1:])
        cur = None

    try:
        fh = open(path, "r", encoding="utf-8", errors="ignore")
    except OSError as exc:
        warnings.append(f"无法读取 {path}：{exc}")
        return

    with fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("$"):
                continue
            upper = line.strip().upper()
            if upper.startswith("BEGIN BULK"):
                in_bulk = True
                for c in flush():
                    yield c
                continue
            if upper.startswith("ENDDATA"):
                for c in flush():
                    yield c
                break
            if upper in ("CEND",) or not in_bulk:
                continue

            m = _INCLUDE_RE.match(line)
            if m:
                for c in flush():
                    yield c
                target = m.group(1)
                inc_path = target if os.path.isabs(target) else os.path.join(base_dir, target)
                if os.path.isfile(inc_path):
                    included.append(os.path.relpath(inc_path, os.path.dirname(path)) if depth else inc_path)
                    for c in _iter_cards(inc_path, warnings, included, depth + 1, in_bulk=True):
                        yield c
                else:
                    warnings.append(f"INCLUDE 目标不存在：{target}")
                continue

            is_cont = (line[:1] in (" ", "\t", "+", "*")) and cur is not None
            if is_cont:
                cur.extend(_continuation_fields(line))
                continue

            fields = _split_fields(line)
            for c in flush():
                yield c
            if not fields or not fields[0]:
                cur = None
                continue
            cur = [fields[0]] + fields[1:]

    for c in flush():
        yield c


def parse_bdf(path: str, include_geometry: bool = False) -> dict[str, Any]:
    """解析 BDF，返回统计摘要（不含 JSON 信封）。"""
    warnings: list[str] = []
    included: list[str] = []
    cards: dict[str, int] = {}
    nodes: dict[int, tuple[float, float, float]] = {}
    elem_by_type: dict[str, int] = {}
    props: list[int] = []
    mats: list[int] = []
    constraints = 0
    loads = 0
    aero = 0
    element_conn: list[dict[str, Any]] = []
    sol, subcases = _scan_case_control(path)

    for name, fields in _iter_cards(path, warnings, included):
        cards[name] = cards.get(name, 0) + 1

        if name == "GRID":
            try:
                nid = int(fields[0])
            except (ValueError, IndexError):
                warnings.append(f"GRID 卡片无法解析：{fields[:5]}")
                continue
            cp = fields[1] if len(fields) > 1 else ""
            if cp not in ("", "0"):
                continue  # 局部坐标系节点不计入全局包围盒
            x = _to_float(fields[2]) if len(fields) > 2 else None
            y = _to_float(fields[3]) if len(fields) > 3 else None
            z = _to_float(fields[4]) if len(fields) > 4 else None
            nodes[nid] = (x or 0.0, y or 0.0, z or 0.0)
        elif name in _ELEM_3D or name in _ELEM_2D or name in _ELEM_1D:
            elem_by_type[name] = elem_by_type.get(name, 0) + 1
            if include_geometry:
                n_nodes = _ELEM_NNODES.get(name, 0)
                ids: list[int] = []
                for f in fields[2:2 + n_nodes]:
                    try:
                        ids.append(int(f))
                    except ValueError:
                        break
                if n_nodes and len(ids) == n_nodes:
                    element_conn.append({"type": name, "nodes": ids})
                else:
                    warnings.append(f"{name} 单元 connectivity 不完整：{fields[:6]}")
        elif name in _PROPS:
            try:
                props.append(int(fields[0]))
            except (ValueError, IndexError):
                pass
        elif name in _MATS:
            try:
                mats.append(int(fields[0]))
            except (ValueError, IndexError):
                pass
        elif name in _CONSTRAINTS:
            constraints += 1
        elif name in _LOADS:
            loads += 1
        elif name in _AERO:
            aero += 1

    elem_total = sum(elem_by_type.values())
    bbox = None
    if nodes:
        xs = [v[0] for v in nodes.values()]
        ys = [v[1] for v in nodes.values()]
        zs = [v[2] for v in nodes.values()]
        bbox = {"xmin": min(xs), "ymin": min(ys), "zmin": min(zs),
                "xmax": max(xs), "ymax": max(ys), "zmax": max(zs)}

    return {
        "sol": sol,
        "subcases": subcases,
        "nodes": len(nodes),
        "elements": elem_total,
        "element_types": elem_by_type,
        "properties": props,
        "materials": mats,
        "constraints": constraints,
        "loads": loads,
        "aero_cards": aero,
        "cards": cards,
        "bbox": bbox,
        "included": included,
        "warnings": warnings,
        "node_xyz": dict(nodes) if include_geometry else {},
        "element_conn": element_conn if include_geometry else [],
    }


def build_bdf_json(path: str, out_dir: str | None = None, with_render: bool = True,
                   with_caption: bool = True, captioner: Any = None) -> dict[str, Any]:
    """把 Nastran BDF 解析为统一信封 JSON（输入侧，结果场为空）。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    info = parse_bdf(path)
    warnings = list(info["warnings"])
    if info["nodes"] == 0:
        warnings.append("未解析到 GRID 节点（可能全部在 INCLUDE 中或格式不受支持）")

    bbox = info["bbox"] or {"xmin": 0.0, "ymin": 0.0, "zmin": 0.0,
                            "xmax": 0.0, "ymax": 0.0, "zmax": 0.0}
    extent = {"x": bbox["xmax"] - bbox["xmin"], "y": bbox["ymax"] - bbox["ymin"],
              "z": bbox["zmax"] - bbox["zmin"]}
    physics = _SOL_PHYSICS.get(info["sol"] or 0, "structural")
    if info["aero_cards"] and physics.startswith("structural"):
        physics = "aeroelastic"

    content: dict[str, Any] = {
        "analysis": {
            "physics": physics,
            "solver": "Nastran BDF (input deck)",
            "models": {"sol": info["sol"], "subcases": info["subcases"]},
            "phases": [],
            "time": {},
            "units": {"note": "BDF 未显式声明单位，按求解器约定"},
            "operating_conditions": {},
        },
        "mesh": {
            "dimension": 3,
            "nodes": info["nodes"],
            "faces": 0,
            "cells": info["elements"],
            "boundary_faces": 0,
            "interior_faces": 0,
            "bbox": bbox,
            "extent": extent,
            "cell_types": info["element_types"],
            "zones": {},
            "quality": {"faces_not_applicable": True, "result_fields": False},
        },
        "setup": {
            "properties": {"count": len(info["properties"]), "ids": info["properties"][:200]},
            "materials": {"count": len(info["materials"]), "ids": info["materials"][:200]},
            "constraints": {"cards": info["constraints"]},
            "loads": {"cards": info["loads"]},
            "aero": {"cards": info["aero_cards"]},
        },
        "field_catalog": {},
        "results": [],
        "zone_results": [],
        "derived": {
            "card_total": sum(info["cards"].values()),
            "included_files": info["included"],
            "top_cards": dict(sorted(info["cards"].items(), key=lambda kv: -kv[1])[:15]),
        },
        "rendered_views": [],
    }
    rendered_views: list[dict[str, Any]] = []
    if with_render:
        try:
            from .preview import preview_bdf

            viz_dir = os.path.join(out_dir or os.path.dirname(path), "viz")
            rendered_views = preview_bdf(path, viz_dir)
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
            "primary_format": "NASTRAN_BDF",
            "files": [{
                "role": "model",
                "path": path,
                "format": "Nastran BDF",
                "bytes": os.path.getsize(path),
                "sha256": file_hash.get("value"),
                "hash_skipped": False,
                "hash_skip_reason": None,
            }],
            "parser": {"name": "cae_pipeline.nastran", "version": "0.2"},
            "parsed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "solver": "Nastran BDF (input deck)",
            "dataset": None,
        },
        "meta": {
            "validation": {"is_valid": True, "warnings": warnings, "errors": []},
            "provenance": {"source_files": [path],
                           "steps": ["bdf_open", "include_expand", "card_parse",
                                     "grid_bbox", "render_views", "visual_describe",
                                     "schema_validate"]},
            "capabilities": {
                "geometry": info["nodes"] > 0,
                "mesh": True,
                "setup": bool(content.get("setup")),
                "results": False,
                "render": bool(rendered_views),
                "vision": bool(content.get("visual")),
            },
            "tags": ["nastran", "bdf", "structural"],
            "domain": "commercial_aircraft",
        },
        "content": content,
    }

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
