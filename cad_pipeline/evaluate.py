"""评测：真实 ABC 样例统计 + 合成标注件的特征检测校验，输出 markdown 报告。"""

from __future__ import annotations

import os
import time
from typing import Any

from .pipeline import parse_cad_step


def _samples(root: str = "data/abc_step/extracted") -> list[str]:
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if n.lower().endswith((".stp", ".step")):
                out.append(os.path.join(dirpath, n))
    return sorted(out)


def _synthetic_check(tmp_dir: str = "data/_eval") -> dict[str, Any]:
    """用带孔长方体做标注校验。"""
    from .features import recognize_features
    from .geometry import extract_geometry, read_step

    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    box = BRepPrimAPI_MakeBox(100.0, 60.0, 20.0).Shape()
    ax = gp_Ax2(gp_Pnt(50.0, 0.0, 10.0), gp_Dir(0.0, 1.0, 0.0))
    cyl = BRepPrimAPI_MakeCylinder(ax, 8.0, 60.0).Shape()
    shape = BRepAlgoAPI_Cut(box, cyl).Shape()
    os.makedirs(tmp_dir, exist_ok=True)
    p = os.path.join(tmp_dir, "labeled.step")
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(p)

    s = read_step(p)
    g = extract_geometry(s)
    feats = recognize_features(g)
    hole_feats = [f for f in feats if f["type"] == "cylindrical" and f["params"].get("subtype") == "hole"]
    r_ok = any(abs(f["params"]["radius"] - 8.0) < 0.5 for f in hole_feats)
    return {
        "faces": g["topology"]["faces"],
        "has_cyl": bool(hole_feats),
        "radius_correct": r_ok,
        "features": len(feats),
    }


def run_evaluation(samples: list[str] | None = None, report: str = "evaluation_report.md") -> dict[str, Any]:
    samples = samples if samples is not None else _samples()
    rows = []
    for p in samples:
        t0 = time.perf_counter()
        try:
            d = parse_cad_step(p, out_dir="data/_eval", with_render=False, with_caption=True)
            ms = (time.perf_counter() - t0) * 1000
            part = d["content"]["parts"][0]
            feats = part["features"]
            rows.append(
                {
                    "file": os.path.basename(os.path.dirname(p)),
                    "valid": d["meta"]["validation"]["is_valid"],
                    "faces": part["topology"]["faces"],
                    "features": len(feats),
                    "cyl": sum(1 for f in feats if f["type"] == "cylindrical"),
                    "pattern": sum(1 for f in feats if f["type"] == "pattern"),
                    "instances": len(d["content"]["assembly"]["instances"]),
                    "caption_len": len(
                        (d.get("content", {}).get("visual") or {}).get("summary", "")
                    ),
                    "ms": round(ms, 1),
                }
            )
        except Exception as exc:
            rows.append({"file": os.path.basename(os.path.dirname(p)), "valid": False, "error": str(exc), "ms": round((time.perf_counter() - t0) * 1000, 1)})

    syn = _synthetic_check()
    success = sum(1 for r in rows if r.get("valid"))
    report_data = {
        "count": len(rows),
        "success": success,
        "success_rate": round(success / max(len(rows), 1), 3),
        "avg_ms": round(sum(r.get("ms", 0) for r in rows) / max(len(rows), 1), 1),
        "total_features": sum(r.get("features", 0) for r in rows),
        "synthetic": syn,
    }
    _write_report(report, rows, report_data)
    return report_data


def _write_report(path: str, rows: list[dict[str, Any]], data: dict[str, Any]) -> None:
    lines = [
        "# CAD 管道评测报告",
        "",
        f"样例数：{data['count']} ｜ 解析成功率：{data['success_rate']} ｜ 平均耗时(无渲染)：{data['avg_ms']} ms",
        f"累计检测特征数：{data['total_features']}",
        "",
        "## 合成标注件校验（长方体 + 贯通孔 r=8）",
        "",
        f"- 面数：{data['synthetic']['faces']}（期望 7）",
        f"- 圆柱孔识别：{data['synthetic']['has_cyl']}（半径 8.0 正确：{data['synthetic']['radius_correct']}）",
        "",
        "## 真实 ABC 样例",
        "",
        "| 模型 | 有效 | 面数 | 特征 | 圆柱 | 阵列 | 装配实例 | 耗时(ms) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        if r.get("valid", False):
            lines.append(f"| {r['file']} | ✅ | {r['faces']} | {r['features']} | {r['cyl']} | {r['pattern']} | {r['instances']} | {r['ms']} |")
        else:
            lines.append(f"| {r['file']} | ❌ | - | - | - | - | - | {r['ms']} |")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[evaluate] 报告已写入：{path}")
