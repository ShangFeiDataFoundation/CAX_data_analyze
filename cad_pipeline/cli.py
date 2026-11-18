"""CAD 管道命令行入口（STEP / IGES）。

示例：
    python -m cad_pipeline.cli data/abc_step/extracted/00000004/...step -o json_out
    python -m cad_pipeline.cli data/abc_step/extracted -o json_out
    python -m cad_pipeline.cli --to-iges data/abc_step/extracted -o data/iges_selfmade --limit 10
    python -m cad_pipeline.cli --selftest
    python -m cad_pipeline.cli --evaluate
    python -m cad_pipeline.cli --serve
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .pipeline import parse_cad_step


def _make_sample_step(path: str) -> str:
    """生成带贯通圆柱孔的长方体 STEP，用于端到端自检 / 标注校验。"""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    box = BRepPrimAPI_MakeBox(100.0, 60.0, 20.0).Shape()
    ax = gp_Ax2(gp_Pnt(50.0, 0.0, 10.0), gp_Dir(0.0, 1.0, 0.0))
    cyl = BRepPrimAPI_MakeCylinder(ax, 8.0, 60.0).Shape()
    shape = BRepAlgoAPI_Cut(box, cyl).Shape()

    os.makedirs(os.path.dirname(path), exist_ok=True)
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    if not writer.Write(path):
        raise RuntimeError("写入自检 STEP 失败")
    return path


def _run_selftest(outdir: str) -> int:
    sample = _make_sample_step(os.path.join(outdir, "sample_box_hole.stp"))
    print(f"[selftest] 生成样例 STEP：{sample}")
    res = parse_cad_step(sample, out_dir=outdir, with_render=True)
    print(json.dumps(res, ensure_ascii=False, indent=2)[:5000])
    print(f"[selftest] 输出：{res.get('_written_to')}")
    return 0


def _run(path: str, outdir: str | None, with_render: bool, with_caption: bool) -> int:
    outdir = outdir or "json_out"
    os.makedirs(outdir, exist_ok=True)
    files: list[str] = []
    if os.path.isdir(path):
        files = [
            os.path.join(root, f)
            for root, _dirs, names in os.walk(path)
            for f in names
            if f.lower().endswith((".stp", ".step", ".igs", ".iges"))
        ]
        if not files:
            print(f"[run] {path} 下没有 .stp/.step/.igs/.iges 文件")
            return 1
    else:
        files = [path]

    n_ok = 0
    for f in sorted(files):
        try:
            res = parse_cad_step(f, out_dir=outdir, with_render=with_render, with_caption=with_caption)
            print(f"[ok] {f} -> {res.get('_written_to')}")
            n_ok += 1
        except Exception as exc:
            print(f"[fail] {f}: {exc}")
    return 0 if n_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CAD STEP -> JSON 解析管道")
    ap.add_argument("path", nargs="?", help="单个 .stp/.step/.igs/.iges 文件或目录")
    ap.add_argument("-o", "--outdir", default=None, help="JSON/渲染输出目录")
    ap.add_argument("--no-render", action="store_true", help="跳过渲染")
    ap.add_argument("--no-caption", action="store_true", help="跳过多模态描述")
    ap.add_argument("--selftest", action="store_true", help="生成样例 STEP 并跑通端到端")
    ap.add_argument("--to-iges", action="store_true", help="把输入 STEP 转换为 IGES（不解析）")
    ap.add_argument("--parity", nargs=2, metavar=("A", "B"),
                    help="比较两个 CAD 文件（如同一零件的 STEP 与 IGES）的解析一致性")
    ap.add_argument("--limit", type=int, default=None, help="--to-iges 批量转换时的文件数上限")
    ap.add_argument("--evaluate", action="store_true", help="运行评测并生成报告")
    ap.add_argument("--serve", action="store_true", help="启动本地 HTTP API")
    args = ap.parse_args(argv)

    if args.serve:
        from .api import create_app
        import uvicorn

        uvicorn.run(create_app(), host="127.0.0.1", port=8000)
        return 0
    if args.evaluate:
        from .evaluate import run_evaluation

        run_evaluation(report="evaluation_report.md")
        return 0
    if args.selftest:
        return _run_selftest(args.outdir or "data")
    if args.to_iges:
        from .convert import step_dir_to_iges, step_to_iges

        if not args.path:
            ap.error("--to-iges 需要输入路径")
        outdir = args.outdir or "data/iges_selfmade"
        if os.path.isdir(args.path):
            outs = step_dir_to_iges(args.path, outdir, limit=args.limit)
        else:
            os.makedirs(outdir, exist_ok=True)
            target = os.path.join(outdir, os.path.splitext(os.path.basename(args.path))[0] + ".igs")
            outs = [step_to_iges(args.path, target)]
        for o in outs:
            print(f"[iges] {o}")
        print(f"[iges] 共 {len(outs)} 个文件 -> {outdir}")
        return 0 if outs else 1
    if args.parity:
        from .parity import compare_cad_documents, format_report

        da = parse_cad_step(args.parity[0], with_render=False, with_caption=False, write_json=False)
        db = parse_cad_step(args.parity[1], with_render=False, with_caption=False, write_json=False)
        result = compare_cad_documents(da, db)
        print(format_report(result))
        return 0 if result["match"] else 2
    if not args.path:
        ap.print_help()
        return 1
    return _run(args.path, args.outdir, with_render=not args.no_render, with_caption=not args.no_caption)


if __name__ == "__main__":
    sys.exit(main())
