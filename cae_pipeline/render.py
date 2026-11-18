"""CAE 结果可视化（matplotlib，离屏）。

图注使用英文，避免无 CJK 字体时的乱码；坐标统一换算为微米（数据集显示单位）。
"""

from __future__ import annotations

import os
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .cff import FluentCase  # noqa: E402
from .mesh import iter_face_geometry  # noqa: E402

FIG_DPI = 130


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def zone_face_centers(case: FluentCase, zone_name: str, chunk_faces: int = 400_000,
                      coords: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """返回某个面 zone 的面心与面积。"""
    zone = case.zone_by_name(zone_name, "faces")
    if zone is None:
        return np.zeros((0, 3)), np.zeros(0)
    pts, areas = [], []
    for chunk in iter_face_geometry(case, chunk_faces=chunk_faces, coords=coords,
                                    face_range=(zone.min_id - 1, zone.max_id)):
        pts.append(chunk.centroids)
        areas.append(chunk.areas)
    if not pts:
        return np.zeros((0, 3)), np.zeros(0)
    return np.concatenate(pts), np.concatenate(areas)


def render_mesh_overview(case: FluentCase, out_path: str, scale: float = 1e6,
                         coords: np.ndarray | None = None) -> str:
    """壁面 zone 面心 x-z 投影 + 域框线，展示多尺度粗糙壁面。"""
    coords = case.coordinates() if coords is None else coords
    pts, _areas = zone_face_centers(case, "wall", coords=coords)
    bbox = coords.min(axis=0), coords.max(axis=0)
    fig, (ax_full, ax_zoom) = plt.subplots(1, 2, figsize=(11.0, 4.0),
                                           gridspec_kw={"width_ratios": [1.0, 1.25]})
    if pts.size:
        cores = np.percentile(pts[:, 2], [0.5, 99.5])
        sc = ax_full.scatter(pts[:, 0] * scale, pts[:, 2] * scale, s=0.2, c=pts[:, 2] * scale,
                             cmap="viridis", linewidths=0, alpha=0.85,
                             vmin=cores[0] * scale, vmax=cores[1] * scale)
        ax_full.set_title(f"Wall zone face centres (n={len(pts)})  |  x-z")
        fig.colorbar(sc, ax=ax_full, shrink=0.85, label="z [um]")
        # 粗糙度细节：只放大壁面附近
        zmin = float(pts[:, 2].min())
        zband = float(np.percentile(pts[:, 2] - zmin, 99.0))
        zband = max(zband, 1e-9)
        sel = pts[:, 2] <= zmin + zband
        sub = pts[sel]
        sc2 = ax_zoom.scatter(sub[:, 0] * scale, (sub[:, 2] - zmin) * scale, s=0.6,
                              c=(sub[:, 2] - zmin) * scale, cmap="turbo", linewidths=0)
        ax_zoom.set_ylim(-0.05 * zband * scale, zband * scale * 1.05)
        ax_zoom.set_title(
            f"wall roughness zoom (n={len(sub)}, band <= {zband * scale:.2f} um, "
            f"z exaggeration ~{60 / max(zband * scale, 1e-9):.0f}x)"
        )
        ax_zoom.set_xlabel("x [um]")
        ax_zoom.set_ylabel("z - z_min [um]")
        fig.colorbar(sc2, ax=ax_zoom, shrink=0.85, label="height [um]")
    else:
        ax_full.set_title("Wall zone not found")
        ax_zoom.set_visible(False)
    (x0, y0, z0), (x1, y1, z1) = bbox
    ax_full.add_patch(plt.Rectangle((x0 * scale, z0 * scale), (x1 - x0) * scale, (z1 - z0) * scale,
                                    fill=False, ec="0.6", lw=1.0, ls="--"))
    ax_full.set_xlabel("x [um]")
    ax_full.set_ylabel("z [um]")
    ax_full.set_aspect("equal")
    ax_zoom.set_aspect("auto")
    fig.tight_layout()
    _ensure_dir(os.path.dirname(out_path) or ".")
    fig.savefig(out_path, dpi=FIG_DPI)
    plt.close(fig)
    return out_path


def render_field_slices(case: FluentCase, out_path: str, centroids: np.ndarray,
                        fields: dict[str, np.ndarray], scale: float = 1e6,
                        plane_axis: int = 1, slab_frac: float = 0.012,
                        title: str = "") -> str:
    """在过轴平面（默认 y≈0）做薄层切片，绘制 VOF / |U| / p 云图。"""
    valid = np.isfinite(centroids).all(axis=1)
    axis_vals = np.abs(centroids[valid, plane_axis])
    if axis_vals.size == 0:
        raise ValueError("没有有效的单元重心")
    half = float(np.percentile(axis_vals, slab_frac * 100))
    if half <= 0:
        half = float(axis_vals.mean() or 1e-9)
    sel = valid & (np.abs(centroids[:, plane_axis]) <= half)
    other = [i for i in range(3) if i != plane_axis]
    labels = {0: "x", 1: "y", 2: "z"}

    panels = [(name, arr) for name, arr in fields.items() if arr is not None and arr.size == centroids.shape[0]]
    n = max(len(panels), 1)
    fig, axes = plt.subplots(1, n, figsize=(4.6 * n, 4.0), squeeze=False)
    for ax, (name, arr) in zip(axes[0], panels):
        vals = arr[sel]
        finite = np.isfinite(vals)
        sc = ax.scatter(centroids[sel, other[0]][finite] * scale,
                        centroids[sel, other[1]][finite] * scale,
                        c=vals[finite], s=1.2, cmap="turbo", linewidths=0)
        fig.colorbar(sc, ax=ax, shrink=0.85)
        ax.set_title(f"{name}  (slab |{labels[plane_axis]}|<={half * scale:.3g} um)")
        ax.set_xlabel(f"{labels[other[0]]} [um]")
        ax.set_ylabel(f"{labels[other[1]]} [um]")
        ax.set_aspect("equal")
    if not panels:
        axes[0][0].set_visible(False)
    if title:
        fig.suptitle(title)
    fig.tight_layout()
    _ensure_dir(os.path.dirname(out_path) or ".")
    fig.savefig(out_path, dpi=FIG_DPI)
    plt.close(fig)
    return out_path


def render_field_statistics(out_path: str, fields: dict[str, np.ndarray],
                            title: str = "") -> str:
    """场分布直方图 + 分位数。"""
    panels = [(k, v) for k, v in fields.items() if v is not None and np.isfinite(v).any()]
    n = max(len(panels), 1)
    fig, axes = plt.subplots(1, n, figsize=(4.4 * n, 3.6), squeeze=False)
    for ax, (name, arr) in zip(axes[0], panels):
        vals = arr[np.isfinite(arr)]
        if vals.size > 400_000:
            vals = vals[:: vals.size // 400_000 + 1]
        ax.hist(vals, bins=60, color="#3b6ea5")
        q = np.percentile(vals, [1, 50, 99])
        for value, style in zip(q, ("--", "-", "--")):
            ax.axvline(value, color="crimson", ls=style, lw=1.0)
        ax.set_title(f"{name}\np1={q[0]:.4g}  p50={q[1]:.4g}  p99={q[2]:.4g}", fontsize=9)
        ax.set_ylabel("cells")
    if not panels:
        axes[0][0].set_visible(False)
    if title:
        fig.suptitle(title)
    fig.tight_layout()
    _ensure_dir(os.path.dirname(out_path) or ".")
    fig.savefig(out_path, dpi=FIG_DPI)
    plt.close(fig)
    return out_path


def render_droplet_profile(out_path: str, centroids: np.ndarray, vof: np.ndarray,
                           scale: float = 1e6, bins: int = 40, title: str = "") -> str:
    """液滴径向铺展剖面：每个高度层的最大半径与 VOF 加权半径。"""
    mask = np.isfinite(vof) & (vof > 0.5) & np.isfinite(centroids).all(axis=1)
    if not mask.any():
        raise ValueError("没有 VOF>0.5 的单元")
    pts = centroids[mask]
    w = vof[mask]
    z = pts[:, 2] - pts[:, 2].min()
    r = np.hypot(pts[:, 0], pts[:, 1])
    edges = np.linspace(0, z.max() if z.max() > 0 else 1e-6, bins + 1)
    idx = np.clip(np.digitize(z, edges) - 1, 0, bins - 1)
    r_max = np.zeros(bins)
    r_wavg = np.zeros(bins)
    for b in range(bins):
        sel = idx == b
        if sel.any():
            r_max[b] = r[sel].max()
            r_wavg[b] = np.average(r[sel], weights=w[sel])
        else:  # 空层不连线，避免锯齿
            r_max[b] = np.nan
            r_wavg[b] = np.nan
    centres = (edges[:-1] + edges[1:]) / 2 * scale
    fig, ax = plt.subplots(figsize=(6.2, 4.0))
    ax.plot(np.where(np.isfinite(r_max), r_max * scale, np.nan), centres, "-o", ms=2.5,
            label="max radius per layer")
    ax.plot(np.where(np.isfinite(r_wavg), r_wavg * scale, np.nan), centres, "--", lw=1.2,
            label="VOF-weighted mean radius")
    ax.set_xlabel("radial distance [um]")
    ax.set_ylabel("height above lowest droplet cell [um]")
    ax.set_title(title or "droplet spreading profile (VOF>0.5)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    _ensure_dir(os.path.dirname(out_path) or ".")
    fig.savefig(out_path, dpi=FIG_DPI)
    plt.close(fig)
    return out_path


def render_all(case: FluentCase, out_dir: str, prefix: str, *, centroids: np.ndarray,
               field_arrays: dict[str, np.ndarray], vof_water: np.ndarray | None,
               coords: np.ndarray | None = None, scale: float = 1e6,
               title: str = "") -> list[dict[str, str]]:
    """生成全部图件，返回 [{view, file}]（失败项跳过并记录）。"""
    _ensure_dir(out_dir)
    out: list[dict[str, str]] = []
    jobs: list[tuple[str, Any]] = [
        ("mesh_overview", lambda p: render_mesh_overview(case, p, scale, coords)),
        ("field_slices", lambda p: render_field_slices(case, p, centroids, field_arrays, scale,
                                                       title=title)),
        ("field_statistics", lambda p: render_field_statistics(p, field_arrays, title)),
    ]
    if vof_water is not None:
        jobs.append(("droplet_profile",
                     lambda p: render_droplet_profile(p, centroids, vof_water, scale, title=title)))
    for view, fn in jobs:
        path = os.path.join(out_dir, f"{prefix}_{view}.png")
        try:
            fn(path)
            out.append({"view": view, "file": path})
        except Exception as exc:  # pragma: no cover - 出图失败不应中断解析
            out.append({"view": view, "file": None, "error": str(exc)})  # type: ignore[dict-item]
    return out
