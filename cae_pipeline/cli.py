"""CAE 解析管道命令行入口。

示例：
    python -m cae_pipeline.cli --list
    python -m cae_pipeline.cli K86_S01 -o json_out
    python -m cae_pipeline.cli --group Dry_surface -o json_out        # 批量
    python -m cae_pipeline.cli K86_S01 --no-render --no-caption
    python -m cae_pipeline.cli --evaluate --limit 4
    python -m cae_pipeline.cli --serve
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from .dataset import CaeDataset
from .pipeline import build_cae_json, parse_cae_case, slugify

DEFAULT_DATASET = os.environ.get(
    "CAX_CAE_DATASET",
    os.path.join("CAE_dataset", "Dataset_Numerical_study_on_microdroplets_interaction.zip"),
)


def _make_dataset(args) -> CaeDataset:
    cache = args.cache_dir
    if cache is None and args.access == "extract":
        cache = os.path.join(args.outdir or "json_out", "_cae_cache")
    return CaeDataset(args.dataset, cache_dir=cache, access=args.access,
                      block_mb=args.block_mb)


def _print_cases(ds: CaeDataset) -> int:
    manifest = ds.manifest()
    print(f"数据集：{manifest['source']}（{manifest['kind']}）")
    print(f"条目 {manifest['entries']} 个，解压后 {manifest['uncompressed_bytes'] / 1e9:.3f} GB，"
          f"算例 {len(manifest['cases'])} 个（按需访问，不解压全包）")
    print(f"{'算例':<28} {'case 文件':<40} {'data 文件':<30} {'data MB':>8}")
    for c in manifest["cases"]:
        dat = ", ".join(c["data_files"]) or "-"
        print(f"{c['key']:<28} {c['case_file'] or '-':<40} {dat:<30} {c['data_bytes'] / 1e6:>8.1f}")
    return 0


def _run_one(ds: CaeDataset, key: str, args, quiet: bool = False) -> dict:
    t0 = time.perf_counter()
    doc = parse_cae_case(ds, key, out_dir=args.outdir, with_render=not args.no_render,
                         with_caption=not args.no_caption, write_json=True,
                         hash_members=not args.no_hash)
    dt = time.perf_counter() - t0
    if not quiet:
        mesh = doc["content"]["mesh"]
        print(f"[ok] {key} -> {doc.get('_written_to')} "
              f"({mesh['cells']:,} 单元, {len(doc['content']['results'])} 个场, {dt:.1f}s)")
    return doc


def _run_foreign(args) -> int:
    """解析非 Fluent 数据集的外部文件（CGNS / Nastran BDF）。"""
    from .cgns import build_cgns_json
    from .nastran import build_bdf_json
    from .op2 import build_op2_json

    outdir = args.outdir or "json_out"
    os.makedirs(outdir, exist_ok=True)
    ok = fail = 0
    jobs: list[tuple[str, Any, str]] = []
    for p in args.cgns or []:
        jobs.append((p, build_cgns_json, ""))
    for p in args.bdf or []:
        jobs.append((p, build_bdf_json, "_bdf"))
    for p in args.op2 or []:
        jobs.append((p, build_op2_json, "_op2"))

    for path, builder, suffix_tag in jobs:
        try:
            t0 = time.perf_counter()
            doc = builder(
                path,
                out_dir=outdir,
                with_render=not args.no_render,
                with_caption=not args.no_caption,
            )
            base = os.path.basename(path)
            for suffix in (".gz", ".cgns", ".bdf", ".nas", ".dat", ".op2", ".op4"):
                if base.lower().endswith(suffix):
                    base = base[: -len(suffix)]
            out = os.path.join(outdir, f"{slugify(base)}{suffix_tag}_cae.json")
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
            m = doc["content"]["mesh"]
            print(f"[ok] {path} -> {out} "
                  f"({m['nodes']:,} 节点, {m['cells']:,} 单元, "
                  f"{len(doc['content']['results'])} 个场, {time.perf_counter()-t0:.2f}s)")
            ok += 1
        except Exception as exc:
            print(f"[fail] {path}: {exc}")
            fail += 1
    print(f"完成：成功 {ok}，失败 {fail}")
    return 0 if ok else 1


def _load_cae_any(path: str):
    from .cgns import build_cgns_json
    from .nastran import build_bdf_json
    from .op2 import build_op2_json

    low = path.lower()
    if low.endswith((".cgns", ".cgns.gz")):
        return build_cgns_json(path, with_render=False, with_caption=False)
    if low.endswith((".bdf", ".nas", ".dat")):
        return build_bdf_json(path, with_render=False, with_caption=False)
    if low.endswith((".op2", ".op4")):
        return build_op2_json(path, with_render=False, with_caption=False)
    raise ValueError(f"无法识别格式：{path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CAE (ANSYS Fluent CFF) -> JSON 解析管道")
    ap.add_argument("case", nargs="?", help="算例名（目录名，如 K86_S01）或子路径")
    ap.add_argument("--dataset", default=DEFAULT_DATASET, help="zip 或已解压目录")
    ap.add_argument("-o", "--outdir", default="json_out", help="JSON/图件输出目录")
    ap.add_argument("--list", action="store_true", help="列出数据集中的算例（不解压）")
    ap.add_argument("--group", help="按外层分组批量解析（Dry_surface / Wet_surface）")
    ap.add_argument("--all", action="store_true", help="解析全部算例")
    ap.add_argument("--limit", type=int, default=None, help="批量模式下最多解析多少个")
    ap.add_argument("--no-render", action="store_true", help="跳过出图")
    ap.add_argument("--no-caption", action="store_true", help="跳过多模态描述")
    ap.add_argument("--no-hash", action="store_true", help="跳过 sha256（大文件较慢）")
    ap.add_argument("--access", choices=("stream", "extract"), default="stream",
                    help="stream=包内直接读（默认，零解压）；extract=按需解到缓存目录")
    ap.add_argument("--cache-dir", default=None, help="extract 模式的缓存目录")
    ap.add_argument("--block-mb", type=int, default=8, help="包内随机读的块大小（MiB）")
    ap.add_argument("--data-index", type=int, default=0, help="使用第几个 .dat.h5 快照")
    ap.add_argument("--evaluate", action="store_true", help="运行评测并生成报告")
    ap.add_argument("--report", default=None, help="评测报告输出路径")
    ap.add_argument("--serve", action="store_true", help="启动本地 HTTP API")
    ap.add_argument("--cgns", nargs="+", metavar="FILE", help="解析 CGNS 文件（HDF5 存储，支持 .cgns.gz）")
    ap.add_argument("--bdf", nargs="+", metavar="FILE", help="解析 Nastran BDF 文件（含 INCLUDE 递归）")
    ap.add_argument("--op2", nargs="+", metavar="FILE",
                    help="解析 Nastran OP2 结果（需可选依赖 pyNastran + cpylog + scipy）")
    ap.add_argument("--to-cgns", metavar="BDF", help="把 Nastran BDF 的网格转换为 CGNS/HDF5")
    ap.add_argument("--to-bdf", metavar="CGNS", help="把 CGNS/HDF5 的网格转换为 Nastran BDF")
    ap.add_argument("--parity-cae", nargs=2, metavar=("A", "B"),
                    help="比较两个 CAE 文件（如 BDF 与由它转换出的 CGNS）的网格一致性")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args(argv)

    if args.serve:
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.dataset, out_dir=args.outdir), host=args.host, port=args.port)
        return 0

    if args.to_cgns:
        from .exchange import bdf_to_cgns

        outdir = args.outdir or "json_out"
        os.makedirs(outdir, exist_ok=True)
        base = os.path.splitext(os.path.basename(args.to_cgns))[0]
        target = os.path.join(outdir, f"{slugify(base)}.cgns")
        stats = bdf_to_cgns(args.to_cgns, target)
        print(f"[cgns] {stats['source']} -> {stats['target']}")
        print(f"[cgns] {stats['nodes']:,} 节点 / {stats['elements']:,} 单元 "
              f"{stats['element_types']}，丢弃 {stats['dropped_elements']} 个")
        return 0

    if args.to_bdf:
        from .exchange import cgns_to_bdf

        outdir = args.outdir or "json_out"
        os.makedirs(outdir, exist_ok=True)
        base = os.path.basename(args.to_bdf)
        for suffix in (".gz", ".cgns"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)]
        target = os.path.join(outdir, f"{slugify(base)}.bdf")
        stats = cgns_to_bdf(args.to_bdf, target, limit=args.limit)
        print(f"[bdf] {stats['source']} -> {stats['target']}")
        print(f"[bdf] {stats['nodes']:,} 节点 / {stats['elements']:,} 单元"
              + ("（已截断）" if stats["truncated"] else ""))
        return 0

    if args.parity_cae:
        from .parity import compare_cae_documents, format_report

        da = _load_cae_any(args.parity_cae[0])
        db = _load_cae_any(args.parity_cae[1])
        result = compare_cae_documents(da, db)
        print(format_report(result))
        return 0 if result["match"] else 2

    if args.cgns or args.bdf or args.op2:
        return _run_foreign(args)

    ds = _make_dataset(args)
    try:
        if args.list:
            return _print_cases(ds)
        if args.evaluate:
            from .evaluate import run_evaluation

            run_evaluation(cae=ds, out_dir=args.outdir, limit=args.limit,
                           report=args.report or os.path.join(args.outdir, "cae_evaluation_report.md"),
                           with_render=not args.no_render)
            return 0
        if args.group or args.all:
            cases = [c for c in ds.list_cases() if args.all or c.group == args.group]
            if args.limit:
                cases = cases[: args.limit]
            if not cases:
                print("没有匹配的算例")
                return 1
            ok, fail = 0, 0
            for c in cases:
                try:
                    _run_one(ds, c.key, args)
                    ok += 1
                except Exception as exc:
                    print(f"[fail] {c.key}: {exc}")
                    fail += 1
            print(f"完成：成功 {ok}，失败 {fail}")
            return 0 if ok else 1
        if not args.case:
            ap.print_help()
            return 1
        doc = _run_one(ds, args.case, args)
        if os.environ.get("CAX_CAE_PRINT_JSON"):
            print(json.dumps(doc, ensure_ascii=False, indent=2)[:4000])
        return 0
    finally:
        ds.close()


if __name__ == "__main__":
    sys.exit(main())
