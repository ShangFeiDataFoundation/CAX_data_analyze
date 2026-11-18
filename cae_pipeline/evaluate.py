"""评测：真实算例上的解析成功率/耗时/覆盖率，输出 markdown 报告。"""

from __future__ import annotations

import os
import time
from typing import Any

from .dataset import CaeDataset


def _expected_value_checks(doc: dict[str, Any]) -> dict[str, Any]:
    """对单个算例做「物理自洽」校验（有明确期望值的项）。"""
    mesh = doc["content"]["mesh"]
    derived = doc["content"].get("derived", {})
    checks: dict[str, Any] = {}

    # 1) 网格体积之和 ≈ 计算域体积（占比应在 0.5~1.01 之间）
    frac = mesh.get("quality", {}).get("domain_volume_fraction_of_bbox")
    checks["volume_fraction_plausible"] = bool(frac is not None and 0.5 <= frac <= 1.01)
    checks["volume_fraction"] = frac

    # 2) 单元体积必须为正
    checks["no_nonpositive_volume"] = mesh.get("quality", {}).get("cells_with_nonpositive_volume") == 0

    # 3) 边界条件里的面数应与 zone 表一致
    bc = doc["content"]["setup"]["boundary_conditions"]
    total_bc = sum(b["faces"] for b in bc if b.get("is_boundary"))
    checks["boundary_faces_consistent"] = total_bc == mesh["boundary_faces"]
    checks["boundary_faces"] = total_bc

    # 4) 相体积守恒：VOF 体积之和 ≈ 域体积
    dist = derived.get("phase_distribution") or []
    if dist:
        total = sum(abs(d.get("volume") or 0.0) for d in dist)
        checks["phase_volume_sum"] = total
    # 5) 液滴等效半径 vs 初始球半径（若 case 里定义了 cell register 球）
    droplet = derived.get("droplet")
    regs = (doc["content"]["setup"].get("initial_conditions") or {}).get("cell_registers") or []
    r0 = None
    for r in regs:
        if isinstance(r, dict) and r.get("type") == "sphere" and isinstance(r.get("radius"), (int, float)):
            r0 = float(r["radius"])
    if droplet and r0:
        if droplet.get("metric_scope") == "water_phase_film_and_droplet":
            # 湿表面算例：水相 = 液膜 + 液滴，等效半径不可与初始液滴半径比较
            checks["droplet_radius_check"] = "skipped (wet surface: water phase = film + droplet)"
        else:
            r_eq = droplet.get("equivalent_sphere_radius_m") or 0.0
            checks["droplet_radius_error_rel"] = abs(r_eq - r0) / r0
            checks["droplet_radius_matches_initial_sphere"] = checks["droplet_radius_error_rel"] < 0.25
    return checks


def run_evaluation(cae: CaeDataset | str, out_dir: str = "json_out", limit: int | None = 4,
                   report: str | None = None, with_render: bool = False,
                   groups: tuple[str, ...] = ("Dry_surface", "Wet_surface")) -> dict[str, Any]:
    from .pipeline import build_cae_json

    own = isinstance(cae, str)
    ds = CaeDataset(cae) if own else cae
    report = report or os.path.join(out_dir, "cae_evaluation_report.md")
    try:
        cases = [c for c in ds.list_cases() if not groups or c.group in groups]
        # 每个分组各取前若干个，覆盖 Dry/Wet 两类
        picked: list[Any] = []
        per_group = max(1, (limit or 4) // max(len(groups), 1)) if limit else None
        if per_group:
            for g in groups:
                picked.extend([c for c in cases if c.group == g][:per_group])
        else:
            picked = cases
        if limit:
            picked = picked[:limit]

        rows: list[dict[str, Any]] = []
        for entry in picked:
            t0 = time.perf_counter()
            try:
                doc = build_cae_json(ds, entry, out_dir=out_dir, with_render=with_render,
                                     with_caption=True, hash_members=False)
                dt = time.perf_counter() - t0
                content = doc["content"]
                checks = _expected_value_checks(doc)
                rows.append({
                    "case": entry.key,
                    "valid": doc["meta"]["validation"]["is_valid"],
                    "warnings": doc["meta"]["validation"]["warnings"],
                    "cells": content["mesh"]["cells"],
                    "faces": content["mesh"]["faces"],
                    "fields": len(content["results"]),
                    "zones": len(content["mesh"]["zones"]["faces"]),
                    "phases": len(content["analysis"]["phases"]),
                    "field_catalog": sum(
                        len(v) for ph in content["field_catalog"].get("available", {}).values()
                        for v in ph.values()
                    ),
                    "droplet_volume_m3": (content["derived"].get("droplet") or {}).get("volume_m3"),
                    "wetted_area_m2": (content["derived"].get("wetted_area") or {}).get("wetted_area_m2"),
                    "checks": checks,
                    "seconds": round(dt, 2),
                })
            except Exception as exc:  # pragma: no cover
                rows.append({"case": entry.key, "valid": False, "error": str(exc),
                             "seconds": round(time.perf_counter() - t0, 2)})

        summary = {
            "count": len(rows),
            "success": sum(1 for r in rows if r.get("valid")),
            "success_rate": round(sum(1 for r in rows if r.get("valid")) / max(len(rows), 1), 3),
            "avg_seconds": round(sum(r.get("seconds", 0) for r in rows) / max(len(rows), 1), 2),
            "total_fields": sum(r.get("fields", 0) for r in rows),
            "checks_passed": sum(
                1 for r in rows for k, v in (r.get("checks") or {}).items()
                if isinstance(v, bool) and v
            ),
            "checks_total": sum(
                1 for r in rows for k, v in (r.get("checks") or {}).items() if isinstance(v, bool)
            ),
        }
        _write_report(report, rows, summary)
        return summary
    finally:
        if own:
            ds.close()


def _write_report(path: str, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# CAE 解析管道评测报告",
        "",
        "数据集：`CAE_dataset/Dataset_Numerical_study_on_microdroplets_interaction.zip`"
        "（ANSYS Fluent 2025R1 CFF，18 个算例）",
        "",
        f"本次评测算例数：{summary['count']} ｜ 解析成功率：{summary['success_rate']} ｜ "
        f"平均耗时：{summary['avg_seconds']} s/算例 ｜ 提取场条目：{summary['total_fields']} ｜ "
        f"自洽性检查：{summary['checks_passed']}/{summary['checks_total']} 通过",
        "",
        "## 逐算例结果",
        "",
        "| 算例 | 有效 | 单元数 | 面数 | 提取场 | 相数 | 液滴体积(m³) | 润湿面积(m²) | 耗时(s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        if not r.get("valid"):
            lines.append(f"| {r['case']} | ❌ | - | - | - | - | - | - | {r['seconds']} |")
            continue
        dv = r.get("droplet_volume_m3")
        wa = r.get("wetted_area_m2")
        lines.append(
            f"| {r['case']} | ✅ | {r['cells']:,} | {r['faces']:,} | {r['fields']} | {r['phases']} | "
            f"{dv:.3e} | {wa:.3e} | {r['seconds']} |"
            if dv is not None and wa is not None
            else f"| {r['case']} | ✅ | {r['cells']:,} | {r['faces']:,} | {r['fields']} | {r['phases']} | - | - | {r['seconds']} |"
        )
    lines += ["", "## 物理自洽性检查", "",
              "- 网格单元体积之和 / 包围盒体积 ∈ [0.5, 1.01]：验证体积积分与法向定向正确；",
              "- 无「非正体积」单元：验证散度定理计算无翻向；",
              "- 边界条件面数之和 == 边界面总数：验证 zone 表与拓扑一致；",
              "- 液滴等效球半径 vs case 内初始液滴球半径（`cell-registers`）：验证 VOF 与几何对齐。",
              ""]
    for r in rows:
        checks = r.get("checks") or {}
        if not checks:
            continue
        took = " / ".join(
            f"{k}={'✅' if v else '❌'}" for k, v in checks.items() if isinstance(v, bool)
        )
        extra = ", ".join(
            f"{k}={v:.6g}" for k, v in checks.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        )
        lines.append(f"- `{r['case']}`：{took}" + (f"（{extra}）" if extra else ""))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"[evaluate] 报告已写入：{path}")
