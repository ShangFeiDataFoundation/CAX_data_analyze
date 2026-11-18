"""CGNS 读取（HDF5 存储层）。

CGNS 有两种存储层：
  - HDF5（CGNS 3.x 起的标准映射）——用 h5py 直接读，无需 cgnslib；
  - ADF（早期格式）——非 HDF5，本模块明确拒绝并提示。

节点约定：每个 CGNS 实体是一个 HDF5 group，属性 `label` 给出 SIDS 类型
（CGNSBase_t / Zone_t / Elements_t / FlowSolution_t / BC_t …），标量值放在子节点 `data`。
"""

from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import io
import math
import os
from typing import Any

import h5py
import numpy as np
from cax_core.schema import SCHEMA_VERSION

PARSER_NAME = "cae_pipeline/0.2 (CGNS/HDF5 + h5py)"

# 按单元节点数推断 CGNS 单元类型（connectivity 长度 / 单元数）
_ELEM_BY_NODES = {
    2: "BAR_2",
    3: "TRI_3",
    4: "TETRA_4",
    5: "PYRA_5",
    6: "PENTA_6",
    8: "HEX_8",
    9: "QUAD_9",
    10: "TETRA_10",
    13: "PYRA_13",
    15: "PENTA_15",
    20: "HEX_20",
    27: "HEX_27",
}

# 表面单元（计入 boundary_faces）
_SURFACE_ELEMS = {"TRI_3", "TRI_6", "QUAD_4", "QUAD_8", "QUAD_9", "BAR_2", "BAR_3"}
_VOLUME_ELEMS = {"TETRA_4", "TETRA_10", "PYRA_5", "PYRA_13", "PENTA_6", "PENTA_15", "HEX_8", "HEX_20", "HEX_27"}

# 节点名匹配用（长名优先，避免 TETRA_10 被 TETRA_4 抢先匹配）
_KNOWN_ELEMS = sorted(
    ["BAR_2", "BAR_3", "TRI_3", "TRI_6", "QUAD_4", "QUAD_8", "QUAD_9",
     "TETRA_4", "TETRA_10", "PYRA_5", "PYRA_13", "PENTA_6", "PENTA_15",
     "HEX_8", "HEX_20", "HEX_27"],
    key=len, reverse=True,
)


class CgnsUnsupported(RuntimeError):
    """ADF 存储或其他暂不支持的 CGNS 形态。"""


def _attr_str(obj: Any, key: str) -> str | None:
    v = obj.attrs.get(key)
    if v is None:
        return None
    if isinstance(v, (bytes, np.bytes_)):
        return bytes(v).decode("utf-8", "replace").strip() or None
    return str(v)


def _label(obj: Any) -> str | None:
    return _attr_str(obj, "label")


def _node_name(obj: Any, fallback: str) -> str:
    return _attr_str(obj, "name") or fallback


def _read_scalar_group(group: Any) -> Any:
    """CGNS/HDF5 的标量节点名为 " data"（带前导空格），这里同时兼容 "data"。"""
    if not isinstance(group, h5py.Group):
        return None
    for key in (" data", "data"):
        if key in group:
            return group[key][...]
    return None


def _read_text(group: Any) -> str | None:
    raw = _read_scalar_group(group)
    if raw is None:
        return None
    arr = np.asarray(raw).ravel()
    if arr.dtype.kind in ("i", "u"):
        return bytes(arr.astype(np.uint8)).decode("utf-8", "replace").strip("\x00 ") or None
    return str(arr[0]) if arr.size else None


def _read_array(node: Any) -> np.ndarray | None:
    """读取数组：既接受 CGNS 实体 group（取其 data 子节点），也接受直接传入的 dataset。"""
    if isinstance(node, h5py.Dataset):
        return np.asarray(node[...])
    raw = _read_scalar_group(node)
    if raw is None:
        return None
    return np.asarray(raw)


def _children(group: Any, label: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for key in group:
        child = group[key]
        if isinstance(child, h5py.Group) and _label(child) == label:
            out.append((key, child))
    return out


def _stats(values: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    q = np.percentile(arr, [1, 25, 50, 75, 99])
    return {
        "count": int(arr.size),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "p1": float(q[0]),
        "p25": float(q[1]),
        "p50": float(q[2]),
        "p75": float(q[3]),
        "p99": float(q[4]),
    }


class CgnsFile:
    """打开 .cgns / .cgns.gz（HDF5 存储）并按 CGNS 节点语义读取。"""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._buffer: io.BytesIO | None = None
        if self.path.lower().endswith(".gz"):
            with open(self.path, "rb") as handle:
                self._raw = gzip.decompress(handle.read())
            if self._raw[:4] != b"\x89HDF":
                raise CgnsUnsupported(
                    "该 CGNS 使用 ADF 存储（非 HDF5），当前读取器不支持；"
                    "需引入 cgnslib 或自实现 ADF 解析"
                )
            self._buffer = io.BytesIO(self._raw)
            self.h5 = h5py.File(self._buffer, "r", driver="fileobj")
        else:
            with open(self.path, "rb") as f:
                magic = f.read(8)
            if magic[:4] != b"\x89HDF":
                raise CgnsUnsupported(
                    "该 CGNS 使用 ADF 存储（非 HDF5），当前读取器不支持；"
                    "需引入 cgnslib 或自实现 ADF 解析"
                )
            self.h5 = h5py.File(self.path, "r")

    def close(self) -> None:
        try:
            self.h5.close()
        except Exception:
            pass

    # --- 结构 ---------------------------------------------------------

    def bases(self) -> list[tuple[str, Any]]:
        out = _children(self.h5, "CGNSBase_t")
        if out:
            return out
        # 容错：部分写出器不写 Base 的 label，用"含 Zone_t 子节点"判定
        extra: list[tuple[str, Any]] = []
        for key in self.h5:
            node = self.h5[key]
            if isinstance(node, h5py.Group) and _children(node, "Zone_t"):
                extra.append((key, node))
        return extra

    def zones(self, base: Any) -> list[tuple[str, Any]]:
        return _children(base, "Zone_t")

    def zone_arrays(self, zone: Any) -> tuple[np.ndarray, list[tuple[str, np.ndarray]]]:
        """返回 (节点坐标 (N,3), [(单元类型, connectivity (M,npe) 0-based)])。"""
        coords: dict[str, np.ndarray] = {}
        if "GridCoordinates" in zone:
            for cname, cgroup in _children(zone["GridCoordinates"], "DataArray_t"):
                arr = _read_array(cgroup)
                if arr is not None:
                    coords[cname] = arr.ravel()
        if not {"CoordinateX", "CoordinateY", "CoordinateZ"} <= coords.keys():
            return np.zeros((0, 3)), []
        xyz = np.column_stack([coords["CoordinateX"], coords["CoordinateY"], coords["CoordinateZ"]])

        out: list[tuple[str, np.ndarray]] = []
        for ename, egroup in _children(zone, "Elements_t"):
            rng = _read_array(egroup["ElementRange"]) if "ElementRange" in egroup else None
            conn = _read_array(egroup["ElementConnectivity"]) if "ElementConnectivity" in egroup else None
            if conn is None or rng is None:
                continue
            count = int(np.asarray(rng).ravel()[1] - np.asarray(rng).ravel()[0] + 1)
            if count <= 0 or conn.size % count:
                continue
            etype = None
            for cand in _KNOWN_ELEMS:
                if cand.replace("_", "") in ename.upper().replace("_", ""):
                    etype = cand
                    break
            if etype is None:
                etype = _ELEM_BY_NODES.get(int(round(conn.size / count)), "UNKNOWN")
            arr = np.asarray(conn, dtype=np.int64).reshape(count, -1) - 1  # 转 0-based
            out.append((etype, arr))
        return xyz, out

    # --- 几何 ---------------------------------------------------------

    def zone_info(self, name: str, zone: Any) -> dict[str, Any]:
        zname = _node_name(zone, name)
        zone_type = _read_text(zone["ZoneType"]) if "ZoneType" in zone else None
        sizes = _read_array(zone) if (" data" in zone or "data" in zone) else None
        vertex_sizes = [int(x) for x in np.asarray(sizes)[0].ravel()] if sizes is not None else []
        cell_sizes = [int(x) for x in np.asarray(sizes)[1].ravel()] if sizes is not None else []

        coords: dict[str, np.ndarray] = {}
        if "GridCoordinates" in zone:
            for cname, cgroup in _children(zone["GridCoordinates"], "DataArray_t"):
                arr = _read_array(cgroup)
                if arr is not None:
                    coords[cname] = arr.ravel()

        nodes = int(coords["CoordinateX"].size) if "CoordinateX" in coords else (
            int(np.prod(vertex_sizes)) if vertex_sizes else 0)

        elements: list[dict[str, Any]] = []
        for ename, egroup in _children(zone, "Elements_t"):
            rng = _read_array(egroup["ElementRange"]) if "ElementRange" in egroup else None
            conn = _read_array(egroup["ElementConnectivity"]) if "ElementConnectivity" in egroup else None
            count = int(rng.ravel()[1] - rng.ravel()[0] + 1) if rng is not None else 0
            etype = None
            for cand in _KNOWN_ELEMS:
                if cand.replace("_", "") in ename.upper().replace("_", ""):
                    etype = cand
                    break
            if etype is None and count and conn is not None:
                npe = int(round(conn.size / count))
                etype = _ELEM_BY_NODES.get(npe, f"NPE_{npe}")
            elements.append({
                "name": _node_name(egroup, ename),
                "type": etype or "UNKNOWN",
                "count": count,
                "range": [int(x) for x in np.asarray(rng).ravel()] if rng is not None else None,
                "connectivity_entries": int(conn.size) if conn is not None else 0,
            })

        vertex_cells = 0
        if zone_type and zone_type.lower().startswith("struct"):
            dims = vertex_sizes or [1]
            vertex_cells = int(np.prod([max(d - 1, 1) for d in dims]))
        else:
            volume = sum(e["count"] for e in elements if e["type"] in _VOLUME_ELEMS)
            # 纯壳/梁网格（无体单元）时，把所有单元计为 cells
            vertex_cells = volume if volume else sum(e["count"] for e in elements)
        surface_cells = sum(e["count"] for e in elements if e["type"] in _SURFACE_ELEMS)

        bbox = None
        if {"CoordinateX", "CoordinateY", "CoordinateZ"} <= coords.keys():
            bbox = {
                "xmin": float(coords["CoordinateX"].min()), "xmax": float(coords["CoordinateX"].max()),
                "ymin": float(coords["CoordinateY"].min()), "ymax": float(coords["CoordinateY"].max()),
                "zmin": float(coords["CoordinateZ"].min()), "zmax": float(coords["CoordinateZ"].max()),
            }

        fields = []
        for fname, fgroup in _children(zone, "FlowSolution_t"):
            location = _read_text(fgroup["GridLocation"]) if "GridLocation" in fgroup else "Vertex"
            for aname, agroup in _children(fgroup, "DataArray_t"):
                arr = _read_array(agroup)
                if arr is None:
                    continue
                fields.append({
                    "name": _node_name(agroup, aname),
                    "location": location,
                    "count": int(arr.size),
                    "data": arr,
                })

        bcs = []
        if "ZoneBC" in zone:
            for bname, bgroup in _children(zone["ZoneBC"], "BC_t"):
                pr = _read_array(bgroup["PointRange"]) if "PointRange" in bgroup else None
                bcs.append({
                    "name": _node_name(bgroup, bname),
                    "type": _read_text(bgroup),
                    "point_range": pr.tolist() if pr is not None else None,
                    "location": _read_text(bgroup["GridLocation"]) if "GridLocation" in bgroup else None,
                })

        return {
            "name": zname,
            "type": zone_type or "Unstructured",
            "vertex_sizes": vertex_sizes,
            "cell_sizes": cell_sizes,
            "nodes": nodes,
            "cells": vertex_cells,
            "surface_elements": surface_cells,
            "elements": elements,
            "bbox": bbox,
            "fields": fields,
            "boundary_conditions": bcs,
        }


def _sha256(path: str) -> dict[str, Any]:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"algorithm": "sha256", "value": h.hexdigest(), "bytes": os.path.getsize(path)}


def build_cgns_json(path: str, out_dir: str | None = None, with_render: bool = True,
                    with_caption: bool = True, captioner: Any = None) -> dict[str, Any]:
    """把 HDF5 存储的 CGNS 解析为统一信封 JSON。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    warnings: list[str] = []
    cg = CgnsFile(path)
    try:
        bases = cg.bases()
        if not bases:
            raise ValueError("CGNS 中未找到 Base 节点")
        version = None
        if "CGNSLibraryVersion" in cg.h5:
            v = _read_array(cg.h5["CGNSLibraryVersion"])
            if v is not None and v.size:
                version = f"{float(np.asarray(v).ravel()[0]):g}"

        zones: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        bcs_all: list[dict[str, Any]] = []
        total_nodes = total_cells = total_surface = 0
        cell_types: dict[str, int] = {}
        bbox = {"xmin": math.inf, "ymin": math.inf, "zmin": math.inf,
                "xmax": -math.inf, "ymax": -math.inf, "zmax": -math.inf}
        have_bbox = False

        for bname, base in bases:
            for zname, zone in cg.zones(base):
                info = cg.zone_info(zname, zone)
                fields = info.pop("fields")
                bcs = info.pop("boundary_conditions")
                zones[info["name"]] = {
                    k: v for k, v in info.items() if k != "bbox"
                }
                total_nodes += info["nodes"]
                total_cells += info["cells"]
                total_surface += info["surface_elements"]
                volume_elems = [e for e in info["elements"] if e["type"] in _VOLUME_ELEMS]
                # 体单元优先；纯壳/梁网格（无体单元）则把所有单元计入类型分布
                for e in (volume_elems or info["elements"]):
                    cell_types[e["type"]] = cell_types.get(e["type"], 0) + e["count"]
                if info["type"].lower().startswith("struct") and info["cells"]:
                    cell_types["STRUCTURED"] = cell_types.get("STRUCTURED", 0) + info["cells"]
                if info["bbox"]:
                    bb = info["bbox"]
                    have_bbox = True
                    bbox["xmin"] = min(bbox["xmin"], bb["xmin"])
                    bbox["ymin"] = min(bbox["ymin"], bb["ymin"])
                    bbox["zmin"] = min(bbox["zmin"], bb["zmin"])
                    bbox["xmax"] = max(bbox["xmax"], bb["xmax"])
                    bbox["ymax"] = max(bbox["ymax"], bb["ymax"])
                    bbox["zmax"] = max(bbox["zmax"], bb["zmax"])

                for f in fields:
                    results.append({
                        "field": f["name"],
                        "semantic": f["name"],
                        "phase": 0,
                        "location": f["location"],
                        "zone": info["name"],
                        "stats": _stats(f["data"]),
                    })
                for bc in bcs:
                    bc = dict(bc)
                    bc["zone"] = info["name"]
                    bcs_all.append(bc)

        if not have_bbox:
            bbox = {"xmin": 0.0, "ymin": 0.0, "zmin": 0.0, "xmax": 0.0, "ymax": 0.0, "zmax": 0.0}

        extent = {
            "x": bbox["xmax"] - bbox["xmin"],
            "y": bbox["ymax"] - bbox["ymin"],
            "z": bbox["zmax"] - bbox["zmin"],
        }
        dims = 3 if any(v > 0 for v in (extent["y"], extent["z"])) else 2

        models: dict[str, Any] = {}
        for bname, base in bases:
            if "FlowEquationSet" in base:
                fes = base["FlowEquationSet"]
                if "GoverningEquations" in fes:
                    models["governing_equations"] = _read_text(fes["GoverningEquations"])
                if "TurbulenceModel" in fes:
                    models["turbulence_model"] = _read_text(fes["TurbulenceModel"])
                if "GasModel" in fes:
                    models["gas_model"] = _read_text(fes["GasModel"])
        units: dict[str, Any] = {}
        for bname, base in bases:
            if "DimensionalUnits" in base:
                u = _read_array(base["DimensionalUnits"])
                if u is not None:
                    units["cgns_dimensional_units"] = [int(x) for x in np.asarray(u).ravel()]

        content: dict[str, Any] = {
            "analysis": {
                "physics": "fluid_flow",
                "solver": f"CGNS {version}" if version else "CGNS",
                "models": models,
                "phases": [],
                "time": {},
                "units": units,
                "operating_conditions": {},
            },
            "mesh": {
                "dimension": dims,
                "nodes": total_nodes,
                "faces": total_surface,
                "cells": total_cells,
                "boundary_faces": total_surface,
                "interior_faces": 0,
                "bbox": bbox,
                "extent": extent,
                "cell_types": cell_types,
                "zones": zones,
                "quality": {"storage": "HDF5", "faces_estimated": total_surface == 0},
            },
            "setup": {"boundary_conditions": bcs_all, "units": units},
            "field_catalog": {
                "fields": [{"name": r["field"], "zone": r["zone"], "location": r["location"],
                            "count": r["stats"].get("count", 0)} for r in results],
            },
            "results": results,
            "zone_results": [],
            "derived": {"zone_count": len(zones), "base_count": len(bases)},
            "rendered_views": [],
        }
        rendered_views: list[dict[str, Any]] = []
        if with_render:
            try:
                from .preview import preview_cgns

                viz_dir = os.path.join(out_dir or os.path.dirname(path), "viz")
                rendered_views = preview_cgns(path, viz_dir)
            except Exception as exc:
                warnings.append(f"渲染失败：{exc}")
        from cax_core.assets import render_assets

        content["rendered_views"] = render_assets(rendered_views, out_dir)
        if with_caption:
            from .multimodal import describe

            content["visual"] = describe(content, rendered_views, captioner=captioner)
        else:
            content["visual"] = None
    finally:
        cg.close()

    file_hash = _sha256(path)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "data_type": "cae",
        "source": {
            "primary_format": "CGNS",
            "files": [{
                "role": "model",
                "path": path,
                "format": "CGNS",
                "bytes": os.path.getsize(path),
                "sha256": file_hash.get("value"),
                "hash_skipped": False,
                "hash_skip_reason": None,
            }],
            "parser": {"name": "cae_pipeline.cgns", "version": "0.2"},
            "parsed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "solver": f"CGNS {version}" if version else "CGNS",
            "dataset": None,
        },
        "meta": {
            "validation": {"is_valid": True, "warnings": warnings, "errors": []},
            "provenance": {"source_files": [path],
                           "steps": ["cgns_open", "cgns_bases", "cgns_zones", "cgns_mesh",
                                     "cgns_fields", "render_views", "visual_describe",
                                     "schema_validate"]},
            "capabilities": {
                "geometry": total_nodes > 0,
                "mesh": True,
                "setup": bool(bcs_all),
                "results": bool(results),
                "render": bool(rendered_views),
                "vision": bool(content.get("visual")),
            },
            "tags": ["cgns", "hdf5", "cfd"],
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
