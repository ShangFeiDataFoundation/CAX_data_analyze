"""CAD 格式转换：STEP -> IGES（用于自造跨格式回归集）。"""

from __future__ import annotations

import os

from OCP.IGESControl import IGESControl_Writer

from .geometry import read_step


def step_to_iges(step_path: str, iges_path: str | None = None) -> str:
    """把 STEP 转为 IGES，返回输出路径。"""
    step_path = os.path.abspath(step_path)
    if iges_path is None:
        iges_path = os.path.splitext(step_path)[0] + ".igs"
    os.makedirs(os.path.dirname(os.path.abspath(iges_path)), exist_ok=True)

    shape = read_step(step_path)
    writer = IGESControl_Writer()
    writer.AddShape(shape)
    writer.ComputeModel()
    if not writer.Write(iges_path):
        raise RuntimeError(f"写入 IGES 失败：{iges_path}")
    return iges_path


def step_dir_to_iges(src_dir: str, out_dir: str, limit: int | None = None) -> list[str]:
    """批量把目录下的 STEP 转为 IGES，返回输出文件列表。"""
    files = sorted(
        os.path.join(root, f)
        for root, _dirs, names in os.walk(src_dir)
        for f in names
        if f.lower().endswith((".stp", ".step"))
    )
    if limit:
        files = files[:limit]
    os.makedirs(out_dir, exist_ok=True)
    out: list[str] = []
    for f in files:
        target = os.path.join(out_dir, os.path.splitext(os.path.basename(f))[0] + ".igs")
        out.append(step_to_iges(f, target))
    return out
