"""网格几何与质量统计（分块、抽样，避免大内存峰值）。"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterator

import numpy as np

from .cff import FluentCase

# Fluent 单元类型码（CFF cell-types 列）常见取值
CELL_TYPE_LABELS = {
    -1: "unknown",
    0: "mixed",
    1: "tri",
    2: "tet",
    3: "quad",
    4: "hex",
    5: "pyramid",
    6: "wedge",
    7: "poly",
}


@dataclasses.dataclass
class FaceGeometryChunk:
    start: int
    stop: int
    centroids: np.ndarray   # (n, 3)
    areas: np.ndarray       # (n,)
    normals: np.ndarray     # (n, 3) 单位法向


def cell_type_histogram(case: FluentCase) -> dict[str, int]:
    codes, counts = np.unique(case.cell_types(), return_counts=True)
    return {
        CELL_TYPE_LABELS.get(int(c), f"type-{int(c)}"): int(n)
        for c, n in zip(codes, counts)
    }


def iter_face_geometry(case: FluentCase, chunk_faces: int = 400_000,
                       coords: np.ndarray | None = None,
                       face_range: tuple[int, int] | None = None) -> Iterator[FaceGeometryChunk]:
    """逐块计算面心/面积/单位法向（Newell 法，按节点连接顺序定向）。"""
    coords = case.coordinates() if coords is None else coords
    flat, nnodes = case.face_nodes()
    n_faces = nnodes.shape[0]
    starts = np.zeros(n_faces, dtype=np.int64)
    np.cumsum(nnodes[:-1], out=starts[1:])
    ends = starts + nnodes
    flat_start_of_face = starts
    last_entry = ends - 1

    first, last = (0, n_faces) if face_range is None else face_range
    first, last = max(0, first), min(n_faces, last)
    for begin in range(first, last, chunk_faces):
        end = min(begin + chunk_faces, last)
        if end <= begin:
            break
        lo = int(starts[begin])
        hi = int(ends[end - 1])
        idx = np.arange(lo, hi, dtype=np.int64)
        n_face_here = end - begin
        face_of_entry = np.repeat(np.arange(begin, end, dtype=np.int64), nnodes[begin:end])
        is_last = np.zeros(idx.shape[0], dtype=bool)
        is_last[last_entry[begin:end] - lo] = True
        nxt = np.where(is_last, flat_start_of_face[face_of_entry], idx + 1)

        p = coords[flat[lo:hi]]
        q = coords[flat[nxt]]
        cross = np.cross(p, q)
        acc = np.add.reduceat(cross, starts[begin:end] - lo, axis=0) * 0.5
        area = np.linalg.norm(acc, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            normals = acc / np.where(area > 0, area, 1.0)[:, None]
        normals[area <= 0] = 0.0
        summed = np.add.reduceat(p, starts[begin:end] - lo, axis=0)
        centroids = summed / nnodes[begin:end, None]
        yield FaceGeometryChunk(begin, end, centroids, area, normals)
        del p, q, cross, acc


@dataclasses.dataclass
class CellGeometry:
    centroids: np.ndarray
    volumes: np.ndarray
    negative_volume_cells: int
    face_areas: np.ndarray          # (n_faces,) float32，1-based 面 id 顺序
    face_area_sample: np.ndarray
    orientation_flipped: bool = False
    zone_areas: dict[str, float] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class MeshSummary:
    data: dict[str, Any]
    cell_geometry: CellGeometry


def compute_cell_geometry(case: FluentCase, chunk_faces: int = 400_000,
                          sample_stride: int = 17,
                          sample_cap: int = 400_000) -> CellGeometry:
    """一次遍历得到单元重心与单元体积（散度定理），并抽样面面积。"""
    ncells = case.cell_count
    nfaces = case.face_count
    c0 = case.face_cells(0)
    c1 = case.face_cells(1)
    vol = np.zeros(ncells, dtype=np.float64)
    wsum = np.zeros(ncells, dtype=np.float64)
    cen = np.zeros((ncells, 3), dtype=np.float64)
    face_areas = np.zeros(nfaces, dtype=np.float32)
    areas: list[np.ndarray] = []
    n_sampled = 0
    zone_ranges = [(z.name, z.min_id - 1, z.max_id) for z in case.face_zones()]
    zone_areas = {name: 0.0 for name, _lo, _hi in zone_ranges}

    for chunk in iter_face_geometry(case, chunk_faces=chunk_faces):
        a = chunk.areas
        fc = chunk.centroids
        face_areas[chunk.start:chunk.stop] = a
        for name, lo, hi in zone_ranges:
            seg_lo, seg_hi = max(chunk.start, lo), min(chunk.stop, hi)
            if seg_hi > seg_lo:
                zone_areas[name] += float(a[seg_lo - chunk.start:seg_hi - chunk.start].sum())
        term = a * np.einsum("ij,ij->i", chunk.normals, fc)
        idx = np.arange(chunk.start, chunk.stop)
        left = c0[idx] - 1
        ok_left = left >= 0
        vol += np.bincount(left[ok_left], weights=term[ok_left], minlength=ncells) / 3.0
        right = c1[idx] - 1
        ok_right = right >= 0
        if ok_right.any():
            vol -= np.bincount(right[ok_right], weights=term[ok_right], minlength=ncells) / 3.0
        for side, mask in ((left, ok_left), (right, ok_right)):
            if not mask.any():
                continue
            sel = side[mask]
            w = a[mask]
            wsum += np.bincount(sel, weights=w, minlength=ncells)
            for k in range(3):
                cen[:, k] += np.bincount(sel, weights=w * fc[mask, k], minlength=ncells)
        if n_sampled < sample_cap:
            take = a[::sample_stride]
            take = take[: max(0, sample_cap - n_sampled)]
            if take.size:
                areas.append(np.array(take, dtype=np.float32))
                n_sampled += take.size

    with np.errstate(invalid="ignore", divide="ignore"):
        cen = cen / np.where(wsum > 0, wsum, 1.0)[:, None]
    cen[wsum <= 0] = np.nan
    # 面法向的「外向」约定在不同导出/网格器下可能相反：按总体积符号自动纠正。
    flipped = bool(np.nansum(vol) < 0)
    if flipped:
        vol = -vol
    negative = int(np.count_nonzero(vol <= 0))
    sample = np.concatenate(areas) if areas else np.zeros(0, dtype=np.float32)
    return CellGeometry(cen, vol, negative, face_areas, sample, flipped, zone_areas)


def _describe(values: np.ndarray, unit: str | None = None) -> dict[str, Any]:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    qs = np.percentile(arr, [1, 25, 50, 75, 99]) if arr.size > 100 else np.percentile(arr, [0, 25, 50, 75, 100])
    out = {
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
    if unit:
        out["unit"] = unit
    return out


def build_mesh_summary(case: FluentCase, chunk_faces: int = 400_000,
                       sample_stride: int = 17,
                       bctype_map: dict[str, str] | None = None) -> MeshSummary:
    """汇总网格规模、zone 表、包围盒、单元类型与质量指标。"""
    coords = case.coordinates()
    geom = compute_cell_geometry(case, chunk_faces=chunk_faces, sample_stride=sample_stride)

    face_zones = []
    for z in case.face_zones():
        face_zones.append(z.to_dict((bctype_map or {}).get(z.name)))
    cell_zones = [z.to_dict((bctype_map or {}).get(z.name)) for z in case.cell_zones()]

    boundary_faces = sum(z["count"] for z in face_zones if z.get("opposite_cell_zone") == 0)
    interior_faces = case.face_count - boundary_faces
    bbox = {
        "xmin": float(coords[:, 0].min()), "xmax": float(coords[:, 0].max()),
        "ymin": float(coords[:, 1].min()), "ymax": float(coords[:, 1].max()),
        "zmin": float(coords[:, 2].min()), "zmax": float(coords[:, 2].max()),
    }
    size = [bbox["xmax"] - bbox["xmin"], bbox["ymax"] - bbox["ymin"], bbox["zmax"] - bbox["zmin"]]
    domain_volume = float(size[0] * size[1] * size[2])
    volumes = geom.volumes

    data: dict[str, Any] = {
        "dimension": case.dimension,
        "nodes": case.node_count,
        "faces": case.face_count,
        "cells": case.cell_count,
        "boundary_faces": int(boundary_faces),
        "interior_faces": int(interior_faces),
        "bbox": bbox,
        "extent": {"dx": size[0], "dy": size[1], "dz": size[2]},
        "cell_types": cell_type_histogram(case),
        "zones": {
            "faces": face_zones,
            "cells": cell_zones,
        },
    }
    for entry in data["zones"]["faces"]:
        entry["total_area"] = float(geom.zone_areas.get(entry["name"], 0.0))
    quality: dict[str, Any] = {
        "face_area_sampled": _describe(geom.face_area_sample),
        "cell_volume": _describe(volumes),
        "cells_with_nonpositive_volume": geom.negative_volume_cells,
        "volume_orientation_flipped": geom.orientation_flipped,
        "cell_volume_uniformity": None,
        "mean_cells_per_face": round(case.face_count / max(case.cell_count, 1), 3),
        "mesh_density_cells_per_unit_volume": None,
    }
    if volumes.size and np.isfinite(volumes).any():
        finite = volumes[np.isfinite(volumes)]
        mean_v = float(finite.mean())
        if mean_v > 0:
            quality["cell_volume_uniformity"] = round(float(finite.std() / mean_v), 4)
            quality["mesh_density_cells_per_unit_volume"] = round(1.0 / mean_v, 3)
            quality["domain_volume_fraction_of_bbox"] = round(float(finite.sum() / domain_volume), 6)
    data["quality"] = quality
    return MeshSummary(data, geom)
