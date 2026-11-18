"""生成自包含的 CAX 管线演示页（真实解析结果 + 内嵌渲染图）。

运行：python demo/build_demo.py
输出：demo/pipeline_demo.html（双击即可打开，无需服务器）
"""

from __future__ import annotations

import base64
import glob
import html
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cad_pipeline.parity import compare_cad_documents  # noqa: E402
from cad_pipeline.pipeline import parse_cad_step  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO = Path(__file__).resolve().parent


def img_uri(path: Path) -> str:
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- CAD
def collect_cad() -> list[dict]:
    out = []
    for p in sorted((ROOT / "data" / "_step_recheck").glob("*_cad.json")):
        doc = load_json(p)
        part = doc["content"]["parts"][0]
        stem = p.name[: -len("_cad.json")]
        feats = part.get("features", [])
        out.append({
            "label": stem.split("_")[0],
            "file": doc["source"]["filename"],
            "format": doc["source"]["format"],
            "faces": part["topology"]["faces"],
            "edges": part["topology"]["edges"],
            "vertices": part["topology"]["vertices"],
            "solids": part["topology"]["solids"],
            "volume": part["mass_props"]["volume"],
            "area": part["mass_props"]["surface_area"],
            "feature_count": len(feats),
            "feature_types": dict(Counter(f["type"] for f in feats)),
            "face_types": [(t["type"], t["count"]) for t in part["face_types"]],
            "sample_features": [
                {"type": f["type"], "subtype": f["params"].get("subtype"),
                 "radius": f["params"].get("radius"), "confidence": f["confidence"]}
                for f in feats[:6]
            ],
            "images": {
                v: img_uri(ROOT / "json_out" / "renders" / f"{stem}_{v}.png")
                for v in ("iso", "front", "top", "right")
            },
        })
    return out


def collect_parity(cad_parts: list[dict]) -> list[dict]:
    rows = []
    for p in sorted((ROOT / "data" / "_iges_test").glob("*_cad.json")):
        stem = p.name[: -len("_cad.json")]
        if stem == "sample_box_hole":
            continue
        step_doc = load_json(ROOT / "data" / "_step_recheck" / p.name)
        iges_doc = load_json(p)
        r = compare_cad_documents(step_doc, iges_doc)
        rows.append({
            "name": stem.split("_")[0],
            "match": r["match"],
            "volume_a": r["volume_a"],
            "volume_b": r["volume_b"],
            "features": r["features_b"],
            "warnings": r["warnings"],
        })
    # 合成标注件：STEP 现场解析，IGES 用已有 JSON
    sample_step = ROOT / "data" / "sample_box_hole.stp"
    sample_iges = ROOT / "data" / "_regress" / "sample_box_hole_cad.json"
    if sample_step.exists() and sample_iges.exists():
        a = parse_cad_step(str(sample_step), with_render=False, with_caption=False, write_json=False)
        b = load_json(sample_iges)
        r = compare_cad_documents(a, b)
        rows.append({
            "name": "sample_box_hole",
            "match": r["match"],
            "volume_a": r["volume_a"],
            "volume_b": r["volume_b"],
            "features": r["features_b"],
            "warnings": r["warnings"],
        })
    return rows


def collect_nasa() -> dict:
    p = ROOT / "data" / "_nasa_crm" / "ASSY 8-1-11_cad.json"
    if not p.exists():
        return {}
    doc = load_json(p)
    part = doc["content"]["parts"][0]
    return {
        "name": doc["content"]["product"]["name"] or "ASSY 8-1-11",
        "units": doc["content"]["product"]["units"],
        "faces": part["topology"]["faces"],
        "solids": part["topology"]["solids"],
        "edges": part["topology"]["edges"],
        "vertices": part["topology"]["vertices"],
        "volume": part["mass_props"]["volume"],
        "area": part["mass_props"]["surface_area"],
        "features": len(part["features"]),
        "face_types": [(t["type"], t["count"]) for t in part["face_types"][:5]],
        "size_mb": round(p.stat().st_size / 1e6, 2),
    }


# ---------------------------------------------------------------- CAE
def collect_cae() -> list[dict]:
    out = []
    for name, label in (("Dry_surface__K86_S01", "初始态 · 未铺展"),
                        ("Dry_surface__K45_S005", "撞击后 · 已铺展")):
        p = ROOT / "json_out" / f"{name}_cae.json"
        if not p.exists():
            continue
        doc = load_json(p)
        c = doc["content"]
        drop = c["derived"].get("droplet") or {}
        out.append({
            "label": label,
            "case": name.replace("Dry_surface__", ""),
            "cells": c["mesh"]["cells"],
            "nodes": c["mesh"]["nodes"],
            "fields": len(c["results"]),
            "ratio": drop.get("spreading_ratio"),
            "diameter_um": (drop.get("diameter_m") or 0) * 1e6,
            "height_um": (drop.get("height_m") or 0) * 1e6,
            "solver": c["analysis"]["solver"],
            "images": {
                v: img_uri(ROOT / "json_out" / "viz" / f"{name}_{v}.png")
                for v in ("mesh_overview", "field_slices", "field_statistics", "droplet_profile")
            },
        })
    return out


def collect_foreign() -> tuple[list[dict], list[dict]]:
    cgns, bdf = [], []
    for p in sorted((ROOT / "data" / "_cae_foreign").glob("*_cae.json")):
        doc = load_json(p)
        fmt = doc["source"]["format"]
        c = doc["content"]
        row = {
            "name": doc["source"]["filename"][:34],
            "format": fmt,
            "nodes": c["mesh"]["nodes"],
            "cells": c["mesh"]["cells"],
            "fields": len(c["results"]),
            "extra": "",
            "valid": doc["meta"]["validation"]["is_valid"],
        }
        if fmt.startswith("CGNS"):
            row["extra"] = f"{len(c['mesh']['zones'])} zones"
            cgns.append(row)
        elif fmt == "Nastran BDF":
            if not p.name.endswith("_bdf_cae.json"):
                continue  # 跳过早期无后缀命名的重复产物
            sol = (c["analysis"]["models"] or {}).get("sol")
            row["extra"] = f"SOL {sol}" if sol is not None else "—"
            bdf.append(row)
    return cgns, bdf


def collect_op2() -> list[dict]:
    """OP2 结果表（应力/单元力/模态/特征值）。"""
    out = []
    for p in sorted((ROOT / "data" / "_cae_foreign").glob("*_op2_cae.json")):
        doc = load_json(p)
        c = doc["content"]
        tables = c["derived"].get("result_tables", [])
        out.append({
            "name": doc["source"]["filename"],
            "nodes": c["mesh"]["nodes"],
            "cells": c["mesh"]["cells"],
            "modes": len(c["derived"].get("modes") or []),
            "eigen": len(c["derived"].get("eigenvalues") or []),
            "tables": [
                {"object": t["object"], "kind": t["kind"], "count": t["count"],
                 "components": t["components"], "max": t.get("max"),
                 "max_component": t.get("max_component")}
                for t in tables
            ],
        })
    return out


def collect_formats(cad_parts: list[dict], cae: list[dict]) -> list[dict]:
    """六种格式的图鉴：典型场景 / 通常包含 / 本管线输出。"""
    step_img = cad_parts[0]["images"]["iso"] if cad_parts else ""
    cae_img = cae[1]["images"]["field_slices"] if len(cae) > 1 else ""
    return [
        {
            "title": "STEP", "badge": "设计交付",
            "image": step_img,
            "when": "详细设计定型后，设计部门向仿真、工艺、供应商交付模型；跨企业协同与适航资料归档。AP203/AP214 主要承载几何与装配，AP242 用于带三维标注的 MBD。",
            "contains": "B-Rep 拓扑（实体/壳/面/边）、装配结构与位姿、单位、产品名与作者；AP242 还带尺寸公差、表面粗糙度和材料标注。通常不保留参数化建模历史。",
            "ours": "拓扑与质量属性、面类型分布、孔/凸台/阵列/圆角/倒角、装配位姿、四视图渲染。",
        },
        {
            "title": "IGES", "badge": "历史/曲面交换",
            "image": img_uri(ROOT / "data" / "_previews" / "iges_sample_iso.png"),
            "when": "老型号与老系统数据、气动外形的曲面交换、只支持 IGES 的软件对接。航空外形数据至今仍大量以 IGES 归档。",
            "contains": "曲面片（B 样条、圆锥曲面等）、曲线与少量标注；<b>没有拓扑</b>，没有实体与装配概念；单位与比例写在全局节。转换中解析曲面常退化成旋转面。",
            "ours": "全局节单位/比例解析、曲面退化还原、面片自动缝合重建实体，并可与 STEP 做一致性比对。",
        },
        {
            "title": "Fluent CFF", "badge": "CFD 求解",
            "image": cae_img,
            "when": "CFD 求解完成后，Fluent 导出的 case + data（.cas.h5 / .dat.h5）；用于流场归档、后处理和仿真数据管理。",
            "contains": "网格与 zone、求解设置（以 Scheme 文本嵌入）、边界与初始条件、多相与材料、压力/速度/VOF 等结果场、时间步与单位。",
            "ours": "在 zip 内零解压流式读取，输出网格质量、边界条件、42 个场统计、液滴铺展等派生量和切片云图。",
        },
        {
            "title": "CGNS", "badge": "跨求解器交换",
            "image": img_uri(ROOT / "data" / "_previews" / "yf17_hdf5_iso.png"),
            "when": "跨求解器交换与归档 CFD 结果（NASA、DLR、SU2、OpenFOAM 等均支持）；风洞-仿真对比、网格数据共享。",
            "contains": "Base/Zone 层级、结构或非结构网格、FlowSolution 场、边界条件、气动系数与参考状态；存储层有早期的 ADF 和现在的 HDF5 两种。",
            "ours": "读取 HDF5 存储层：Base/Zone、单元、场统计、边界条件与求解模型；5 个官方样例通过。",
        },
        {
            "title": "Nastran BDF", "badge": "结构输入",
            "image": img_uri(ROOT / "data" / "_previews" / "freedlm_iso.png"),
            "when": "结构有限元分析的输入：机体/机翼强度、模态、颤振分析；主机厂与供应商之间交换有限元模型。",
            "contains": "GRID 节点、单元（CQUAD4/CTETRA/CBAR…）、属性（PSHELL/PBAR）、材料（MAT1）、载荷与约束，以及 CAERO/AESURF/TRIM 等气动卡片和求解序列。",
            "ours": "递归展开 INCLUDE，统计节点/单元/属性/材料/约束/载荷/气动卡片，输出包围盒，并可转换为 CGNS 做一致性校验。",
        },
        {
            "title": "Nastran OP2", "badge": "结构结果",
            "image": img_uri(ROOT / "data" / "_previews" / "plate_iso.png"),
            "when": "Nastran 求解后的二进制结果，通常与 BDF 一起交付；用于强度校核、模态/颤振评估和疲劳分析。",
            "contains": "几何规模、位移、特征向量、复特征值，以及按单元类型组织的应力、应变、单元力、应变能等结果表。",
            "ours": "位移/模态/特征值 + 应力与单元力表；plate 的最大 von Mises 418.0055 与 .f06 文本结果一致。",
        },
    ]


def envelope_snippet(cad_parts: list[dict]) -> str:
    doc = load_json(ROOT / "data" / "_step_recheck" / "00000004_1ffb81a71e5b402e966b9341_step_003_cad.json")
    part = doc["content"]["parts"][0]
    slim = {
        "schema_version": doc["schema_version"],
        "data_type": doc["data_type"],
        "source": {k: doc["source"][k] for k in ("filename", "format", "bytes", "parser")},
        "meta": {
            "validation": doc["meta"]["validation"],
            "provenance": {"steps": doc["meta"]["provenance"]["steps"]},
            "tags": doc["meta"]["tags"],
        },
        "content": {
            "product": doc["content"]["product"],
            "parts": [{
                "id": part["id"], "name": part["name"],
                "topology": part["topology"],
                "face_types": part["face_types"][:3],
                "features": part["features"][:1],
            }],
        },
    }
    return json.dumps(slim, ensure_ascii=False, indent=2)


def main() -> int:
    cad_parts = collect_cad()
    parity = collect_parity(cad_parts)
    nasa = collect_nasa()
    cae = collect_cae()
    cgns, bdf = collect_foreign()
    op2 = collect_op2()
    formats = collect_formats(cad_parts, cae)
    envelope = envelope_snippet(cad_parts)

    data = {"cad": cad_parts, "parity": parity, "nasa": nasa, "cae": cae,
            "cgns": cgns, "bdf": bdf, "op2": op2, "formats": formats}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    env_html = html.escape(envelope)

    total_features = sum(p["feature_count"] for p in cad_parts)
    parity_ok = sum(1 for r in parity if r["match"])

    page = TEMPLATE
    for key, value in (
        ("__PAYLOAD__", payload),
        ("__ENVELOPE__", env_html),
        ("__CAD_COUNT__", str(len(cad_parts))),
        ("__TOTAL_FEATURES__", str(total_features)),
        ("__PARITY_OK__", str(parity_ok)),
        ("__PARITY_TOTAL__", str(len(parity))),
        ("__CGNS_COUNT__", str(len(cgns))),
        ("__BDF_COUNT__", str(len(bdf))),
        ("__OP2_COUNT__", str(len(op2))),
    ):
        page = page.replace(key, value)
    out = DEMO / "pipeline_demo.html"
    out.write_text(page, encoding="utf-8")
    print(f"[demo] {out}  ({out.stat().st_size/1e6:.2f} MB)")
    print(f"[demo] CAD {len(cad_parts)} 件 / 特征 {total_features}；一致性 {parity_ok}/{len(parity)}；"
          f"CGNS {len(cgns)}；BDF {len(bdf)}；OP2 {len(op2)}")
    return 0


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CAX 解析管线演示</title>
<style>
  :root{
    --bg:#0b1020; --panel:#121a30; --panel2:#0e1527; --line:#243050;
    --text:#e8eefc; --muted:#93a1c0; --accent:#5b8cff; --accent2:#ffb454;
    --ok:#3ddc97; --warn:#ffcc66; --mono:"Cascadia Code",Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#1b2a52 0,var(--bg) 55%);
       color:var(--text);font:15px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
  .wrap{max-width:1180px;margin:0 auto;padding:0 22px 80px}
  header{padding:56px 0 26px}
  .kicker{color:var(--accent);letter-spacing:.18em;font-size:12px;text-transform:uppercase}
  h1{font-size:38px;margin:10px 0 8px;letter-spacing:-.5px}
  .sub{color:var(--muted);max-width:820px}
  .chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
  .chip{background:#16203a;border:1px solid var(--line);border-radius:999px;
        padding:6px 13px;font-size:13px;color:var(--muted)}
  .chip b{color:var(--text)}
  section{margin-top:44px}
  h2{font-size:21px;margin:0 0 6px;display:flex;align-items:center;gap:10px}
  h2 .dot{width:9px;height:9px;border-radius:50%;background:var(--accent);box-shadow:0 0 12px var(--accent)}
  h2.cae .dot{background:var(--accent2);box-shadow:0 0 12px var(--accent2)}
  .lead{color:var(--muted);margin:0 0 18px;max-width:900px}
  .panel{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
         border-radius:16px;padding:20px}
  .flow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0}
  .flow .node{background:#16203a;border:1px solid var(--line);border-radius:10px;
              padding:9px 12px;font-size:13.5px;white-space:nowrap}
  .flow .node.src{border-color:#3c5ea8;background:#182b4f}
  .flow .node.out{border-color:#2f7d63;background:#123024}
  .flow .arrow{color:#4d5f8a}
  .lanes{display:grid;gap:14px}
  .lane{display:grid;grid-template-columns:120px 1fr;gap:12px;align-items:center}
  .lane .tag{font-weight:600;font-size:13px;color:var(--accent)}
  .lane.cae .tag{color:var(--accent2)}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:18px}
  .stat{background:#101a31;border:1px solid var(--line);border-radius:14px;padding:14px 16px}
  .stat .v{font-size:24px;font-weight:700;letter-spacing:-.3px}
  .stat .k{color:var(--muted);font-size:12.5px;margin-top:2px}
  .split{display:grid;grid-template-columns:300px 1fr;gap:18px}
  .picker{display:flex;flex-direction:column;gap:8px}
  .pick{display:flex;gap:10px;align-items:center;background:#101a31;border:1px solid var(--line);
        border-radius:12px;padding:8px;cursor:pointer;transition:.15s;text-align:left;color:inherit}
  .pick:hover{border-color:#3c5ea8;transform:translateY(-1px)}
  .pick.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
  .pick img{width:52px;height:52px;object-fit:contain;background:#0a1224;border-radius:8px}
  .pick .n{font-weight:600}
  .pick .m{color:var(--muted);font-size:12px}
  .views{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .view{background:#0a1224;border:1px solid var(--line);border-radius:12px;overflow:hidden}
  .view img{width:100%;height:150px;object-fit:contain;display:block}
  .view img{cursor:zoom-in;transition:transform .18s ease}
  .view img:hover{transform:scale(1.03)}
  .view .cap{font-size:12px;color:var(--muted);padding:5px 8px;border-top:1px solid var(--line)}
  .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-top:12px}
  .metric{background:#0d1528;border:1px solid var(--line);border-radius:10px;padding:10px 12px}
  .metric .v{font-size:18px;font-weight:650}
  .metric .k{font-size:12px;color:var(--muted)}
  table{width:100%;border-collapse:collapse;font-size:13.5px}
  th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:500;font-size:12.5px}
  td.mono,.mono{font-family:var(--mono);font-size:12.5px}
  .ok{color:var(--ok)} .bad{color:#ff7b7b}
  .tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
  .tagx{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--muted)}
  pre{background:#080e1c;border:1px solid var(--line);border-radius:12px;padding:16px;
      overflow:auto;max-height:460px;font-family:var(--mono);font-size:12.5px;line-height:1.55;color:#c9d6f2}
  .hero-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
  .hl{background:#0f1b33;border:1px solid #2b4478;border-radius:14px;padding:16px}
  .hl h3{margin:0 0 8px;font-size:15px}
  footer{margin-top:56px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:18px}
  code{font-family:var(--mono);background:#0a1224;border:1px solid var(--line);border-radius:6px;padding:1px 6px}
  .lightbox{position:fixed;inset:0;z-index:99;display:none;align-items:center;justify-content:center;
    background:rgba(5,9,18,.93);backdrop-filter:blur(6px)}
  .lightbox.on{display:flex}
  .lightbox img{max-width:92vw;max-height:80vh;cursor:grab;user-select:none;transition:transform .06s linear}
  .lightbox img.dragging{cursor:grabbing;transition:none}
  .lb-cap{position:absolute;top:20px;left:24px;color:var(--text);font-size:14px}
  .lb-close{position:absolute;top:14px;right:24px;font-size:30px;line-height:1;color:var(--muted);cursor:pointer}
  .lb-arrow{position:absolute;top:50%;transform:translateY(-50%);font-size:36px;color:var(--muted);
    cursor:pointer;padding:10px 16px;user-select:none}
  .lb-arrow.prev{left:8px} .lb-arrow.next{right:8px}
  .lb-bar{position:absolute;bottom:22px;left:50%;transform:translateX(-50%);display:flex;gap:12px;
    background:#101a31;border:1px solid var(--line);border-radius:999px;padding:7px 14px;font-size:12.5px;color:var(--muted)}
  .fmt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px}
  .fmt-card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:16px;overflow:hidden;display:flex;flex-direction:column}
  .fmt-img{background:#f6f8fc;height:200px;display:flex;align-items:center;justify-content:center;overflow:hidden}
  .fmt-img img{width:100%;height:100%;object-fit:contain;cursor:zoom-in;transition:transform .18s ease}
  .fmt-img img:hover{transform:scale(1.03)}
  .fmt-body{padding:14px 16px 18px}
  .fmt-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
  .fmt-head h3{margin:0;font-size:16px}
  .badge{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--muted);white-space:nowrap}
  .fmt-body p{margin:0 0 9px;font-size:13px;line-height:1.65;color:var(--muted)}
  .fmt-body p b{color:var(--text);font-weight:600;margin-right:8px}
  .fmt-body p.ours{color:#b9c9ea}
  @media (max-width:900px){.split{grid-template-columns:1fr}.views{grid-template-columns:repeat(2,1fr)}.lane{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="kicker">CAX Data Pipeline</div>
  <h1>CAX 解析管线演示</h1>
  <p class="sub">两条独立通道、六种输入格式、统一 JSON 信封。页面里的数字与图片全部来自本仓库的真实解析产物。</p>
  <div class="chips">
    <span class="chip">CAD <b>__CAD_COUNT__</b> 件 · 特征 <b>__TOTAL_FEATURES__</b></span>
    <span class="chip">STEP/IGES 一致性 <b>__PARITY_OK__/__PARITY_TOTAL__</b></span>
    <span class="chip">CAE Fluent <b>18</b> 算例</span>
    <span class="chip">CGNS <b>__CGNS_COUNT__</b> 样例</span>
    <span class="chip">Nastran BDF <b>__BDF_COUNT__</b> 样例</span>
    <span class="chip">Nastran OP2 <b>__OP2_COUNT__</b> 样例</span>
  </div>
</header>

<section>
  <h2><span class="dot"></span>管线结构</h2>
  <p class="lead">CAD 与 CAE 各自独立解析，在输出层统一为 <code>schema_version / data_type / source / meta / content</code> 五段信封，并保留处理链溯源。</p>
  <div class="panel">
    <div class="lanes">
      <div class="lane">
        <div class="tag">CAD 通道</div>
        <div class="flow">
          <span class="node src">STEP / IGES</span><span class="arrow">→</span>
          <span class="node">头段元数据 · 单位</span><span class="arrow">→</span>
          <span class="node">BRep 拓扑与质量属性</span><span class="arrow">→</span>
          <span class="node">特征识别（带置信度）</span><span class="arrow">→</span>
          <span class="node">装配位姿 · 四视图</span><span class="arrow">→</span>
          <span class="node out">JSON</span>
        </div>
      </div>
      <div class="lane cae">
        <div class="tag" style="color:var(--accent2)">CAE 通道</div>
        <div class="flow">
          <span class="node src">CFF / CGNS / BDF / OP2</span><span class="arrow">→</span>
          <span class="node">格式读取（零解压·INCLUDE）</span><span class="arrow">→</span>
          <span class="node">网格·设置·结果场</span><span class="arrow">→</span>
          <span class="node">统计·派生量</span><span class="arrow">→</span>
          <span class="node">可视化·描述</span><span class="arrow">→</span>
          <span class="node out">JSON</span>
        </div>
      </div>
    </div>
    <div class="stats">
      <div class="stat"><div class="v">2</div><div class="k">独立通道</div></div>
      <div class="stat"><div class="v">6</div><div class="k">输入格式（STEP / IGES / CFF / CGNS / BDF / OP2）</div></div>
      <div class="stat"><div class="v">100%</div><div class="k">输出通过 pydantic 信封校验</div></div>
      <div class="stat"><div class="v">0</div><div class="k">全量解压（CFD 直接在 zip 内随机读）</div></div>
    </div>
  </div>
</section>

<section>
  <h2><span class="dot"></span>格式图鉴：什么时候出现、里面有什么</h2>
  <p class="lead">同样是"CAX 数据"，不同格式承担的角色差别很大：有的承载设计意图，有的只保存网格，有的专门装结果。下面按六种格式说明典型场景、常见内容，以及本管线实际抽取到什么。</p>
  <div class="fmt-grid" id="fmtGrid"></div>
</section>

<section>
  <h2><span class="dot"></span>CAD：零件级解析与特征识别</h2>
  <p class="lead">选择零件查看四视图与解析指标。几何量来自 BRep，特征为几何级启发式识别（孔 / 凸台 / 阵列 / 圆角 / 倒角），每项带置信度。</p>
  <div class="split">
    <div class="picker" id="cadPicker"></div>
    <div class="panel">
      <div class="views" id="cadViews"></div>
      <div class="metrics" id="cadMetrics"></div>
      <div class="tags" id="cadTags"></div>
    </div>
  </div>
</section>

<section>
  <h2><span class="dot"></span>跨格式一致性：STEP ↔ IGES</h2>
  <p class="lead">同一零件转换成 IGES 后重新解析，比较拓扑、体积、面类型与特征数。IGES 无拓扑信息，接缝边/顶点数会有差异，单独标注为提示。</p>
  <div class="panel"><table id="parityTable"></table></div>
</section>

<section>
  <h2><span class="dot"></span>真实整机装配（NASA CRM）</h2>
  <div class="panel" id="nasaPanel"></div>
</section>

<section>
  <h2 class="cae"><span class="dot"></span>CAE：流体仿真与派生量</h2>
  <p class="lead">Fluent CFF 直接在压缩包内随机读取，输网格质量、求解设置、边界条件与 42 个结果场，并计算液滴铺展等派生量。</p>
  <div class="panel">
    <div class="flow" id="caePicker" style="margin-bottom:14px"></div>
    <div class="views" id="caeViews"></div>
    <div class="metrics" id="caeMetrics"></div>
  </div>
</section>

<section>
  <h2 class="cae"><span class="dot"></span>跨求解器：CGNS 与 Nastran BDF</h2>
  <p class="lead">同一套信封承载不同求解器的数据。CGNS 先支持 HDF5 存储层；BDF 为输入侧，支持 INCLUDE 递归展开。</p>
  <div class="hero-grid">
    <div class="panel"><h3 style="margin-top:0">CGNS（HDF5）</h3><table id="cgnsTable"></table></div>
    <div class="panel"><h3 style="margin-top:0">Nastran BDF</h3><table id="bdfTable"></table></div>
  </div>
</section>

<section>
  <h2 class="cae"><span class="dot"></span>OP2 结果表（应力 / 单元力 / 模态）</h2>
  <p class="lead">同一份 OP2 里可以并存多类结果表。下表列出各文件实际解析到的表、分量数与最大值；CQUAD4 应力的分量标签已按 .f06 结果校验。</p>
  <div class="panel"><table id="op2Table"></table></div>
</section>

<section>
  <h2><span class="dot"></span>统一信封示例</h2>
  <p class="lead">下面是一个真实 CAD 输出的裁剪片段（保留结构、去掉重复内容）。</p>
  <pre>__ENVELOPE__</pre>
</section>

<footer>
  生成时间 2026-10-21 · 数据来源：<code>data/_step_recheck</code>、<code>json_out</code>、<code>data/_cae_foreign</code>、<code>data/_nasa_crm</code><br>
  重新生成：<code>python demo/build_demo.py</code>
</footer>
</div>

<div class="lightbox" id="lb">
  <div class="lb-cap" id="lbCap"></div>
  <span class="lb-close" id="lbClose">×</span>
  <span class="lb-arrow prev" id="lbPrev">‹</span>
  <img id="lbImg" alt="">
  <span class="lb-arrow next" id="lbNext">›</span>
  <div class="lb-bar"><span id="lbZoom">100%</span><span>滚轮缩放 · 拖动平移 · ←/→ 切换 · Esc 关闭</span></div>
</div>

<script>
const DATA = __PAYLOAD__;
const nf = n => new Intl.NumberFormat('zh-CN').format(n);
const num = (x,d=3) => (x===null||x===undefined) ? '—' : Number(x).toLocaleString('zh-CN',{maximumFractionDigits:d});
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function renderCad(i){
  const p = DATA.cad[i];
  document.querySelectorAll('#cadPicker .pick').forEach((el,k)=>el.classList.toggle('active',k===i));
  document.getElementById('cadViews').innerHTML = ['front','top','right','iso'].map(v=>`
    <div class="view"><img src="${p.images[v]}" alt="${v}"><div class="cap">${v}</div></div>`).join('');
  document.getElementById('cadMetrics').innerHTML = [
    ['面 / 边 / 顶点', `${nf(p.faces)} / ${nf(p.edges)} / ${nf(p.vertices)}`],
    ['实体数', nf(p.solids)],
    ['体积', num(p.volume)],
    ['表面积', num(p.area)],
    ['特征数', nf(p.feature_count)],
  ].map(([k,v])=>`<div class="metric"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');
  document.getElementById('cadTags').innerHTML =
    Object.entries(p.feature_types).map(([k,v])=>`<span class="tagx">${esc(k)} × ${v}</span>`).join('') +
    p.face_types.map(([k,v])=>`<span class="tagx">面 ${esc(k)} × ${v}</span>`).join('');
}

// 格式图鉴
document.getElementById('fmtGrid').innerHTML = DATA.formats.map(f => `
  <div class="fmt-card">
    <div class="fmt-img"><img src="${f.image}" alt="${esc(f.title)} · 预览"></div>
    <div class="fmt-body">
      <div class="fmt-head"><h3>${esc(f.title)}</h3><span class="badge">${esc(f.badge)}</span></div>
      <p><b>典型场景</b>${f.when}</p>
      <p><b>通常包含</b>${f.contains}</p>
      <p class="ours"><b>本管线</b>${esc(f.ours)}</p>
    </div>
  </div>`).join('');

function renderCae(i){
  const c = DATA.cae[i];
  document.querySelectorAll('#caePicker .pick').forEach((el,k)=>el.classList.toggle('active',k===i));
  document.getElementById('caeViews').innerHTML = [
    ['mesh_overview','网格 / 壁面几何'],['field_slices','过轴切片云图'],
    ['field_statistics','场分布直方图'],['droplet_profile','液滴铺展剖面']
  ].map(([k,cap])=>`<div class="view"><img src="${c.images[k]}" alt="${cap}"><div class="cap">${cap}</div></div>`).join('');
  document.getElementById('caeMetrics').innerHTML = [
    ['单元', nf(c.cells)],['节点', nf(c.nodes)],['结果场', nf(c.fields)],
    ['铺展系数 D/D0', num(c.ratio,4)],['液滴直径', num(c.diameter_um,2)+' µm'],
    ['液滴高度', num(c.height_um,2)+' µm'],
  ].map(([k,v])=>`<div class="metric"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');
}

// CAD 选择器
document.getElementById('cadPicker').innerHTML = DATA.cad.map((p,i)=>`
  <button class="pick" onclick="renderCad(${i})">
    <img src="${p.images.iso}" alt="">
    <span><span class="n">${esc(p.label)}</span><br><span class="m">${p.faces} 面 · ${p.feature_count} 特征</span></span>
  </button>`).join('');
renderCad(0);

// 一致性表
document.getElementById('parityTable').innerHTML = `
  <tr><th>零件</th><th>结果</th><th>体积 STEP</th><th>体积 IGES</th><th>特征数</th><th>提示</th></tr>` +
  DATA.parity.map(r=>`<tr>
    <td class="mono">${esc(r.name)}</td>
    <td class="${r.match?'ok':'bad'}">${r.match?'✓ 一致':'✗ 不一致'}</td>
    <td class="mono">${num(r.volume_a)}</td>
    <td class="mono">${num(r.volume_b)}</td>
    <td>${r.features}</td>
    <td style="color:var(--muted)">${r.warnings.length? r.warnings.map(esc).join('<br>') : '—'}</td>
  </tr>`).join('');

// NASA CRM
const n = DATA.nasa;
if (n && n.faces) {
  document.getElementById('nasaPanel').innerHTML = `
    <div style="display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div><h3 style="margin:0 0 4px">${esc(n.name)}</h3>
        <div style="color:var(--muted);font-size:13px">整机装配 IGES · 单位 ${esc(n.units)} · JSON ${n.size_mb} MB</div></div>
      <div class="tags" style="margin:0">${n.face_types.map(([k,v])=>`<span class="tagx">${esc(k)} × ${nf(v)}</span>`).join('')}</div>
    </div>
    <div class="stats" style="margin-top:14px">
      <div class="stat"><div class="v">${nf(n.faces)}</div><div class="k">面</div></div>
      <div class="stat"><div class="v">${nf(n.solids)}</div><div class="k">实体</div></div>
      <div class="stat"><div class="v">${nf(n.features)}</div><div class="k">识别特征</div></div>
      <div class="stat"><div class="v">${num(n.volume,0)}</div><div class="k">体积（in³）</div></div>
    </div>`;
}

// CAE 选择器
document.getElementById('caePicker').innerHTML = DATA.cae.map((c,i)=>`
  <button class="pick" style="width:auto;flex:0 0 auto" onclick="renderCae(${i})">
    <span><span class="n">${esc(c.case)}</span><br><span class="m">${esc(c.label)} · ${nf(c.cells)} 单元</span></span>
  </button>`).join('');
renderCae(0);

// CGNS / BDF
document.getElementById('cgnsTable').innerHTML =
  `<tr><th>文件</th><th>节点</th><th>单元</th><th>场</th><th>Zone</th></tr>` +
  DATA.cgns.map(r=>`<tr><td class="mono">${esc(r.name)}</td><td>${nf(r.nodes)}</td><td>${nf(r.cells)}</td><td>${r.fields}</td><td>${esc(r.extra)}</td></tr>`).join('');
document.getElementById('bdfTable').innerHTML =
  `<tr><th>文件</th><th>节点</th><th>单元</th><th>求解序列</th></tr>` +
  DATA.bdf.map(r=>`<tr><td class="mono">${esc(r.name)}</td><td>${nf(r.nodes)}</td><td>${nf(r.cells)}</td><td>${esc(r.extra)}</td></tr>`).join('');

// OP2 结果表
{
  const rows = [];
  DATA.op2.forEach(f => {
    if (!f.tables.length) {
      rows.push(`<tr><td class="mono">${esc(f.name)}</td><td colspan="5" style="color:var(--muted)">${f.modes} 阶模态 / ${f.eigen} 个特征值</td></tr>`);
    } else {
      f.tables.forEach(t => rows.push(`<tr>
        <td class="mono">${esc(f.name)}</td><td>${esc(t.object)}</td><td>${esc(t.kind)}</td>
        <td>${nf(t.count)}</td><td>${t.components}</td>
        <td class="mono">${num(t.max,3)}${t.max_component!=null?` <span style="color:var(--muted)">(${esc(t.max_component)})</span>`:''}</td>
      </tr>`));
    }
  });
  document.getElementById('op2Table').innerHTML =
    `<tr><th>文件</th><th>对象</th><th>类型</th><th>条目</th><th>分量</th><th>最大值</th></tr>` + rows.join('');
}

// 图片放大查看
const lb = document.getElementById('lb'), lbImg = document.getElementById('lbImg');
let lbGroup = [], lbIdx = 0, lbScale = 1, lbX = 0, lbY = 0, lbDrag = null;
function lbApply(){
  lbImg.style.transform = `translate(${lbX}px,${lbY}px) scale(${lbScale})`;
  document.getElementById('lbZoom').textContent = Math.round(lbScale*100) + '%';
}
function lbShow(){
  const im = lbGroup[lbIdx];
  if (!im) return;
  lbImg.src = im.src;
  document.getElementById('lbCap').textContent = im.alt || '';
  lbScale = 1; lbX = 0; lbY = 0; lbApply();
}
function lbOpen(img){
  const box = img.closest('.views') || img.closest('.fmt-grid');
  lbGroup = box ? [...box.querySelectorAll('img')] : [img];
  lbIdx = Math.max(0, lbGroup.indexOf(img));
  lbShow(); lb.classList.add('on');
}
function lbClose(){ lb.classList.remove('on'); }
function lbStep(d){ if (lbGroup.length) { lbIdx = (lbIdx + d + lbGroup.length) % lbGroup.length; lbShow(); } }
document.addEventListener('click', e => {
  if (e.target.matches('.view img, .fmt-img img')) lbOpen(e.target);
  else if (e.target.id === 'lbClose') lbClose();
  else if (e.target.id === 'lbPrev') lbStep(-1);
  else if (e.target.id === 'lbNext') lbStep(1);
  else if (e.target.id === 'lb') lbClose();
});
document.addEventListener('keydown', e => {
  if (!lb.classList.contains('on')) return;
  if (e.key === 'Escape') lbClose();
  if (e.key === 'ArrowLeft') lbStep(-1);
  if (e.key === 'ArrowRight') lbStep(1);
});
lbImg.addEventListener('wheel', e => {
  e.preventDefault();
  lbScale = Math.min(6, Math.max(1, lbScale * (e.deltaY < 0 ? 1.15 : 0.87)));
  if (lbScale === 1) { lbX = 0; lbY = 0; }
  lbApply();
}, {passive:false});
lbImg.addEventListener('mousedown', e => {
  if (lbScale <= 1) return;
  lbDrag = {x: e.clientX - lbX, y: e.clientY - lbY};
  lbImg.classList.add('dragging');
});
window.addEventListener('mousemove', e => {
  if (!lbDrag) return;
  lbX = e.clientX - lbDrag.x; lbY = e.clientY - lbDrag.y; lbApply();
});
window.addEventListener('mouseup', () => { lbDrag = null; lbImg.classList.remove('dragging'); });

// 调试/演示快捷键：URL 加 #zoom 直接打开第一张 CAD 图的放大层
if (location.hash === '#zoom') setTimeout(() => lbOpen(document.querySelector('.view img')), 300);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
