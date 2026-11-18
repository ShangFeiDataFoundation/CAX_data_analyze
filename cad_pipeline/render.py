"""多视角渲染：OCP 三角化 -> 二进制 STL -> 出图。

优先用 VTK 离屏（桌面环境效果好）；若离屏失败或出图全白（无可用的 OpenGL 上下文），
自动回退到 matplotlib 软件渲染（解析 STL 三角形，用 Poly3DCollection 绘制）。
"""

from __future__ import annotations

import math
import os
import tempfile
from typing import Any

import numpy as np

# matplotlib 字体缓存写入工作区，避免默认用户目录在沙箱中不可写
_MPLCONF = os.path.abspath(os.path.join(os.getcwd(), ".mplconfig"))
os.environ.setdefault("MPLCONFIGDIR", _MPLCONF)
os.makedirs(_MPLCONF, exist_ok=True)

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.TopoDS import TopoDS_Shape


_VIEWS: dict[str, tuple[float, float]] = {
    "iso": (30.0, 45.0),
    "front": (0.0, 0.0),
    "top": (90.0, 0.0),
    "right": (0.0, 90.0),
}


def tessellate_to_stl(shape: TopoDS_Shape, stl_path: str, deflection: float = 0.05) -> str:
    """三角化并写出二进制 STL，返回路径。"""
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    ok = writer.Write(shape, stl_path)
    if not ok:
        # Some OCCT builds fail on long or non-ASCII target paths. Retry through a
        # temporary file, then move the generated mesh into the intended folder.
        fd, temp_path = tempfile.mkstemp(suffix=".stl")
        os.close(fd)
        try:
            ok = writer.Write(shape, temp_path)
            if ok:
                os.makedirs(os.path.dirname(os.path.abspath(stl_path)), exist_ok=True)
                os.replace(temp_path, stl_path)
        finally:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
    if not ok:
        raise RuntimeError("StlAPI_Writer 写 STL 失败")
    return stl_path


def _parse_stl_binary(path: str) -> np.ndarray:
    """解析二进制 STL，返回 (N,3,3) 三角形数组。"""
    with open(path, "rb") as f:
        data = f.read()
    tri_count = int.from_bytes(data[80:84], "little")
    # 每个三角形：normal(3f)+3*顶点(9f)=12f(48B) + attr(2B) => 50B 记录
    dt = np.dtype([("normal", "<f4", 3), ("verts", "<f4", (3, 3)), ("attr", "<u2")])
    recs = np.frombuffer(data, dtype=dt, count=tri_count, offset=84)
    return recs["verts"].astype(np.float64)


def _render_vtk(stl_path: str, out_png: str, elev: float, azim: float) -> bool:
    """VTK 离屏渲染。返回 False 表示失败或可判定为空白。"""
    try:
        import vtk

        reader = vtk.vtkSTLReader()
        reader.SetFileName(stl_path)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(reader.GetOutputPort())
        mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.72, 0.74, 0.78)
        actor.GetProperty().SetSpecular(0.5)
        actor.GetProperty().SetSpecularPower(20)
        actor.GetProperty().EdgeVisibilityOn()
        actor.GetProperty().SetEdgeColor(0.12, 0.12, 0.14)

        renderer = vtk.vtkRenderer()
        renderer.AddActor(actor)
        renderer.SetBackground(1.0, 1.0, 1.0)
        renderer.ResetCamera()
        # 按视角旋转相机（等价于水平/俯仰角）
        r = azim * math.pi / 180.0
        e = elev * math.pi / 180.0
        dist = 300.0
        renderer.GetActiveCamera().SetPosition(
            dist * math.cos(e) * math.cos(r),
            dist * math.cos(e) * math.sin(r),
            dist * math.sin(e),
        )
        renderer.GetActiveCamera().SetFocalPoint(0.0, 0.0, 0.0)
        renderer.GetActiveCamera().SetViewUp(0.0, 0.0, 1.0)

        rw = vtk.vtkRenderWindow()
        rw.AddRenderer(renderer)
        rw.SetSize(900, 700)
        rw.SetOffScreenRendering(1)
        rw.Render()
        w2i = vtk.vtkWindowToImageFilter()
        w2i.SetInput(rw)
        w2i.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetFileName(out_png)
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.Write()
        rw.Finalize()
        return _image_not_blank(out_png)
    except Exception as exc:  # pragma: no cover - 环境相关
        print(f"[render] VTK 离屏失败：{exc}")
        return False


def _image_not_blank(png: str, min_frac: float = 0.01) -> bool:
    """粗略判定：非纯白/纯背景像素占比是否足够。"""
    try:
        from PIL import Image

        im = Image.open(png).convert("L")
        arr = np.asarray(im)
        nz = np.count_nonzero(arr < 245) / arr.size
        return nz > min_frac
    except Exception:
        return False


def _render_matplotlib(stl_path: str, out_png: str, elev: float, azim: float) -> bool:
    """软件渲染：解析 STL 三角形并用 matplotlib 绘制。"""
    import matplotlib

    matplotlib.use("Agg")
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    import matplotlib.pyplot as plt

    tris = _parse_stl_binary(stl_path)
    if tris.size == 0:
        return False
    tri = tris.reshape(-1, 3, 3)
    # 中心化缩放
    center = tri.reshape(-1, 3).mean(axis=0)
    tri = tri - center
    scale = np.abs(tri).max()
    tri = tri / (scale or 1.0)

    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    # 用面片包络
    mesh = Poly3DCollection(tri, alpha=0.95, linewidths=0.2, edgecolor=(0.1, 0.1, 0.12))
    mesh.set_facecolor((0.66, 0.68, 0.73))
    ax.add_collection3d(mesh)

    # 简单法向着色（每个面根据朝向调亮度）
    fn = np.cross(v1 - v0, v2 - v0)
    fn_norm = fn / (np.linalg.norm(fn, axis=1, keepdims=True) + 1e-12)
    light = np.array([0.3, 0.3, 0.9])
    intensity = (np.clip(fn_norm @ light, 0, 1) * 0.6 + 0.3)[:, None]
    face_colors = np.repeat([[0.66, 0.68, 0.73]], tri.shape[0], axis=0) * intensity
    mesh.set_facecolor(face_colors)

    lim = 1.05
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_zlim(-lim, lim)
    ax.set_box_aspect((1, 1, 1))
    ax.set_axis_off()
    ax.view_init(elev=elev, azim=azim)
    ax.set_proj_type("ortho")
    plt.tight_layout()
    plt.savefig(out_png, dpi=100)
    plt.close(fig)
    return True


def render_views(
    shape: TopoDS_Shape,
    out_dir: str,
    prefix: str,
    views: tuple[str, ...] = tuple(_VIEWS.keys()),
) -> list[dict[str, Any]]:
    """渲染多个视角，返回 [{view, file}]。每个视角先 VTK、空白则 matplotlib。"""
    os.makedirs(out_dir, exist_ok=True)
    stl_path = os.path.join(out_dir, f"{prefix}.stl")
    tessellate_to_stl(shape, stl_path)

    results: list[dict[str, Any]] = []
    for v in views:
        cfg = _VIEWS.get(v)
        if cfg is None:
            continue
        elev, azim = cfg
        png = os.path.join(out_dir, f"{prefix}_{v}.png")
        ok = False
        try:
            ok = _render_vtk(stl_path, png, elev, azim)
        except Exception as exc:  # pragma: no cover
            print(f"[render] VTK 失败：{exc}")
        if not ok:
            try:
                ok = _render_matplotlib(stl_path, png, elev, azim)
            except Exception as exc:  # pragma: no cover
                print(f"[render] matplotlib 失败：{exc}")
        if ok:
            results.append({"view": v, "file": png})

    if not results:
        results.append({"view": "stl_fallback", "file": stl_path})
    return results
