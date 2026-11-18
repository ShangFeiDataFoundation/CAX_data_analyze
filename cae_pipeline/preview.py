"""CAE 网格预览渲染（matplotlib，Agg 后端）。

为 CGNS / Nastran BDF / OP2 生成与 CAD 四视图风格一致的几何预览：
把单元边投影到屏幕平面，按高度或结果值着色。
"""

from __future__ import annotations

import os
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

# 每种单元类型的边（节点索引）
_EDGES = {
    "BAR_2": [(0, 1)],
    "TRI_3": [(0, 1), (1, 2), (2, 0)],
    "QUAD_4": [(0, 1), (1, 2), (2, 3), (3, 0)],
    "TETRA_4": [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)],
    "HEX_8": [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
              (0, 4), (1, 5), (2, 6), (3, 7)],
    "PENTA_6": [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3),
                (0, 3), (1, 4), (2, 5)],
    "PYRA_5": [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)],
}

# BDF 单元名 → 类 CGNS 名（用于取边）
_BDF_AS = {
    "CBAR": "BAR_2", "CBEAM": "BAR_2", "CROD": "BAR_2", "CONROD": "BAR_2",
    "CTUBE": "BAR_2", "CBEND": "BAR_2",
    "CTRIA3": "TRI_3", "CTRIA6": "TRI_3", "CTRIAR": "TRI_3",
    "CQUAD4": "QUAD_4", "CQUAD8": "QUAD_4", "CQUAD": "QUAD_4", "CSHEAR": "QUAD_4",
    "CTETRA": "TETRA_4", "CHEXA": "HEX_8", "CPENTA": "PENTA_6", "CPYRA": "PYRA_5",
}

_VIEWS = {
    "iso": (-55.0, 25.0),
    "front": (0.0, 0.0),
    "top": (0.0, 90.0),
}


def _project(xyz: np.ndarray, view: str) -> np.ndarray:
    shape = xyz.shape
    flat = xyz.reshape(-1, 3)
    yaw, pitch = _VIEWS.get(view, _VIEWS["iso"])
    cy, sy = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    cp, sp = np.cos(np.radians(pitch)), np.sin(np.radians(pitch))
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    rot = rx @ rz
    p = flat @ rot.T
    return np.column_stack([p[:, 0], p[:, 2]]).reshape(shape[:-1] + (2,))


def _edge_segments(nodes: np.ndarray, elements: list[dict],
                   limit: int = 60000) -> tuple[np.ndarray, np.ndarray]:
    segs: list[np.ndarray] = []
    idxs: list[np.ndarray] = []
    total = 0
    for e in elements:
        etype = _BDF_AS.get(e["type"], e["type"])
        pattern = _EDGES.get(etype)
        conn = np.asarray(e["conn"], dtype=np.int64)
        if pattern is None or conn.ndim != 2:
            continue
        remaining = limit - total
        max_rows = remaining // len(pattern)
        if max_rows <= 0:
            break
        conn = conn[:max_rows]
        n_new = len(conn) * len(pattern)
        for a, b in pattern:
            if conn.shape[1] <= max(a, b):
                continue
            segs.append(np.stack([nodes[conn[:, a]], nodes[conn[:, b]]], axis=1))
            idxs.append(np.stack([conn[:, a], conn[:, b]], axis=1))
        total += n_new
    if not segs:
        return np.stack([nodes[:1], nodes[:1]], axis=1), np.zeros((1, 2), dtype=np.int64)
    return np.concatenate(segs, axis=0), np.concatenate(idxs, axis=0)


def render_mesh(
    nodes: np.ndarray,
    elements: list[dict],
    out_path: str,
    title: str = "",
    view: str = "iso",
    color_values: np.ndarray | None = None,
    max_edges: int = 60000,
) -> str:
    """把网格边投影成 2D 线框 PNG。"""
    nodes = np.asarray(nodes, dtype=float)
    segs, node_idx = _edge_segments(nodes, elements, limit=max_edges)
    proj_segs = _project(segs, view)
    if color_values is not None and len(color_values) == len(nodes):
        values = np.asarray(color_values)[node_idx].mean(axis=1)
    else:
        values = segs[:, :, 2].mean(axis=1)

    fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=130)
    fig.patch.set_facecolor("#f6f8fc")
    ax.set_facecolor("#f6f8fc")
    lc = LineCollection(proj_segs, linewidths=0.45, cmap="viridis")
    lc.set_array(values)
    ax.add_collection(lc)
    fig.colorbar(lc, ax=ax, fraction=0.025, pad=0.01, shrink=0.75)

    ax.autoscale()
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=10, color="#33415c")
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


def render_mesh_views(nodes: np.ndarray, elements: list[dict], out_dir: str,
                      prefix: str, title: str = "", color_values: np.ndarray | None = None) -> list[dict]:
    views = []
    os.makedirs(out_dir, exist_ok=True)
    for view in ("iso", "front", "top"):
        path = os.path.join(out_dir, f"{prefix}_{view}.png")
        render_mesh(nodes, elements, path, title=f"{title} · {view}", view=view,
                    color_values=color_values)
        views.append({"view": view, "file": path})
    return views


def _structured_boundary_elements(xyz: np.ndarray,
                                  vertex_sizes: list[int]) -> list[tuple[str, np.ndarray]]:
    """Build drawable faces/lines for an element-free structured CGNS zone."""
    if not vertex_sizes or any(size <= 0 for size in vertex_sizes):
        return []
    dims = tuple(int(size) for size in vertex_sizes)
    if np.prod(dims) != len(xyz):
        return []
    # CGNS stores structured coordinates with the first index varying fastest.
    index = np.arange(len(xyz), dtype=np.int64).reshape(dims, order="F")
    out: list[tuple[str, np.ndarray]] = []
    if len(dims) == 1:
        out.append(("BAR_2", np.column_stack([index[:-1], index[1:]])))
        return out
    if len(dims) == 2:
        nx, ny = dims
        i, j = np.meshgrid(np.arange(nx - 1), np.arange(ny - 1), indexing="ij")
        quads = np.stack([
            index[i, j], index[i + 1, j], index[i + 1, j + 1], index[i, j + 1]
        ], axis=-1).reshape(-1, 4)
        out.append(("QUAD_4", quads))
        return out
    if len(dims) != 3:
        return []

    nx, ny, nz = dims
    j, k = np.meshgrid(np.arange(ny - 1), np.arange(nz - 1), indexing="ij")
    for i in (0, nx - 1):
        quads = np.stack([
            index[i, j, k], index[i, j + 1, k],
            index[i, j + 1, k + 1], index[i, j, k + 1],
        ], axis=-1).reshape(-1, 4)
        out.append(("QUAD_4", quads))
    i, k = np.meshgrid(np.arange(nx - 1), np.arange(nz - 1), indexing="ij")
    for jface in (0, ny - 1):
        quads = np.stack([
            index[i, jface, k], index[i + 1, jface, k],
            index[i + 1, jface, k + 1], index[i, jface, k + 1],
        ], axis=-1).reshape(-1, 4)
        out.append(("QUAD_4", quads))
    i, j = np.meshgrid(np.arange(nx - 1), np.arange(ny - 1), indexing="ij")
    for kface in (0, nz - 1):
        quads = np.stack([
            index[i, j, kface], index[i + 1, j, kface],
            index[i + 1, j + 1, kface], index[i, j + 1, kface],
        ], axis=-1).reshape(-1, 4)
        out.append(("QUAD_4", quads))
    return out


def preview_cgns(cgns_path: str, out_dir: str) -> list[dict]:
    from .cgns import CgnsFile, _SURFACE_ELEMS

    coords, elements = [], []
    cg = CgnsFile(cgns_path)
    try:
        offset = 0
        for _b, base in cg.bases():
            for _z, zone in cg.zones(base):
                xyz, elems = cg.zone_arrays(zone)
                if not xyz.size:
                    continue
                coords.append(xyz)
                info = cg.zone_info(_z, zone)
                if not elems:
                    elems = _structured_boundary_elements(
                        xyz, info.get("vertex_sizes", [])
                    )
                # 优先只画表面单元（蒙皮），否则体单元会画成一团内部三角
                surface = [(t, c) for t, c in elems if t in _SURFACE_ELEMS]
                # 剔除外包线框的大面（远场/包围盒），保留机体表面小三角
                kept = []
                for t, c in surface:
                    if t == "TRI_3" and len(c) > 50:
                        p = xyz[c]
                        area = 0.5 * np.linalg.norm(
                            np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
                        thresh = max(np.median(area) * 20.0, 1e-12)
                        c = c[area <= thresh]
                    kept.append((t, c))
                surface = [(t, c) for t, c in kept if len(c)]
                for etype, conn in (surface or elems):
                    elements.append({"type": etype, "conn": conn + offset})
                offset += len(xyz)
    finally:
        cg.close()
    if not coords:
        raise ValueError("CGNS 中没有坐标可渲染")
    nodes = np.vstack(coords)
    name = os.path.basename(cgns_path).replace(".gz", "").replace(".cgns", "")
    return render_mesh_views(nodes, elements, out_dir, name, title=name)


def preview_bdf(bdf_path: str, out_dir: str) -> list[dict]:
    from .nastran import parse_bdf

    info = parse_bdf(bdf_path, include_geometry=True)
    xyz = info["node_xyz"]
    ids = sorted(xyz)
    index = {nid: i for i, nid in enumerate(ids)}
    nodes = np.array([xyz[n] for n in ids], dtype=float)
    elements = []
    for e in info["element_conn"]:
        if all(n in index for n in e["nodes"]):
            elements.append({"type": e["type"], "conn": np.array([[index[n] for n in e["nodes"]]])})
    if not elements:
        raise ValueError("BDF 中没有可渲染的单元")
    name = os.path.splitext(os.path.basename(bdf_path))[0]
    return render_mesh_views(nodes, elements, out_dir, name, title=name)


def preview_op2(op2_path: str, out_dir: str) -> list[dict]:
    """渲染 OP2 的模型；若有特征向量，叠加第一阶模态的变形形状。"""
    from pyNastran.op2.op2 import read_op2
    try:
        from cpylog import SimpleLogger
        log = SimpleLogger(level="error")
    except Exception:  # pragma: no cover
        log = None
    model = read_op2(op2_path, load_geometry=True, debug=False, build_dataframe=False,
                     mode="msc", log=log)

    nodes_d = model.nodes or {}
    ids = sorted(nodes_d)
    index = {nid: i for i, nid in enumerate(ids)}
    nodes = np.array([np.asarray(nodes_d[n].xyz, float) for n in ids])
    elements = []
    for e in (model.elements or {}).values():
        conn = np.array([[index[n] for n in e.nodes if n in index]], dtype=np.int64)
        if conn.size:
            elements.append({"type": getattr(e, "type", ""), "conn": conn})
    if not elements:
        raise ValueError("OP2 中没有可渲染的单元")

    values = None
    disp_obj = None
    if getattr(model, "eigenvectors", None):
        disp_obj = list(model.eigenvectors.values())[0]
    elif getattr(model, "displacements", None):
        disp_obj = list(model.displacements.values())[0]
    if disp_obj is not None:
        obj = disp_obj
        data = np.asarray(obj.data)
        if data.ndim == 3 and data.shape[-1] >= 3:
            disp = np.abs(data.reshape(-1, data.shape[1], data.shape[-1])[0, :, :3])
            mag_nodes = np.linalg.norm(disp, axis=1)
            ng = getattr(obj, "node_gridtype", None)
            if ng is not None and len(ng) == len(mag_nodes):
                by_id = {int(nid): i for i, nid in enumerate(np.asarray(ng)[:, 0])}
                full = np.zeros((len(nodes), 3), dtype=float)
                mag_full = np.zeros(len(nodes), dtype=float)
                for i, nid in enumerate(ids):
                    j = by_id.get(nid)
                    if j is not None:
                        full[i] = disp[j]
                        mag_full[i] = mag_nodes[j]
                if mag_full.max() > 0:
                    scale = 0.15 * np.ptp(nodes, axis=0).max() / mag_full.max()
                    nodes = nodes + full * scale
                    values = mag_full

    name = os.path.splitext(os.path.basename(op2_path))[0]
    return render_mesh_views(nodes, elements, out_dir, name, title=name,
                             color_values=values)
