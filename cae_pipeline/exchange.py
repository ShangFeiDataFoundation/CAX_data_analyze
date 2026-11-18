"""CAE 跨格式转换：Nastran BDF -> CGNS（用于格式一致性验证）。"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

from .cgns_writer import write_cgns
from .nastran import parse_bdf

CGNS_TO_BDF = {
    "TETRA_4": ("CTETRA", 4), "TETRA_10": ("CTETRA", 10),
    "HEXA_8": ("CHEXA", 8), "HEXA_20": ("CHEXA", 20),
    "PENTA_6": ("CPENTA", 6), "PENTA_15": ("CPENTA", 15),
    "PYRA_5": ("CPYRA", 5), "PYRA_13": ("CPYRA", 13),
    "QUAD_4": ("CQUAD4", 4), "QUAD_8": ("CQUAD8", 8),
    "TRI_3": ("CTRIA3", 3), "TRI_6": ("CTRIA6", 6),
    "BAR_2": ("CBAR", 2),
}

# BDF 单元 -> CGNS 单元类型
BDF_TO_CGNS = {
    "CTETRA": "TETRA_4",
    "CHEXA": "HEXA_8",
    "CPENTA": "PENTA_6",
    "CPYRA": "PYRA_5",
    "CQUAD4": "QUAD_4",
    "CQUAD8": "QUAD_8",
    "CQUAD": "QUAD_4",
    "CTRIA3": "TRI_3",
    "CTRIA6": "TRI_6",
    "CTRIAR": "TRI_3",
    "CSHEAR": "QUAD_4",
    "CBAR": "BAR_2",
    "CBEAM": "BAR_2",
    "CROD": "BAR_2",
    "CONROD": "BAR_2",
    "CTUBE": "BAR_2",
    "CBEND": "BAR_2",
}


def bdf_to_cgns(bdf_path: str, cgns_path: str | None = None,
                limit: int | None = None) -> dict[str, Any]:
    """把 BDF 的网格（GRID + 单元 connectivity）写成 CGNS/HDF5。

    返回转换统计，便于与原始 BDF 解析结果比对。
    """
    bdf_path = os.path.abspath(bdf_path)
    if cgns_path is None:
        cgns_path = os.path.splitext(bdf_path)[0] + ".cgns"

    info = parse_bdf(bdf_path, include_geometry=True)
    xyz = info["node_xyz"]
    raw = info["element_conn"][: limit] if limit else info["element_conn"]

    kept: list[dict[str, Any]] = []
    dropped = 0
    for e in raw:
        ctype = BDF_TO_CGNS.get(e["type"])
        if ctype is None or any(n not in xyz for n in e["nodes"]):
            dropped += 1
            continue
        kept.append({"type": ctype, "nodes": e["nodes"]})
    if not kept:
        raise ValueError("没有可转换的单元（可能缺少 GRID 或单元类型不受支持）")

    # 保留全部 GRID 节点（含未被单元引用的孤立点），使节点计数与原始 BDF 一致
    used = sorted(xyz.keys())
    index = {nid: i for i, nid in enumerate(used)}
    nodes = np.array([xyz[nid] for nid in used], dtype=float)
    elements = [{"type": e["type"], "conn": np.array([index[n] for n in e["nodes"]], dtype=np.int32)}
                for e in kept]

    write_cgns(cgns_path, nodes, elements)
    return {
        "source": bdf_path,
        "target": cgns_path,
        "nodes": len(used),
        "elements": len(kept),
        "dropped_elements": dropped,
        "element_types": dict(sorted({e["type"]: sum(1 for k in kept if k["type"] == e["type"]) for e in kept}.items())),
        "bbox": {
            "xmin": float(nodes[:, 0].min()), "xmax": float(nodes[:, 0].max()),
            "ymin": float(nodes[:, 1].min()), "ymax": float(nodes[:, 1].max()),
            "zmin": float(nodes[:, 2].min()), "zmax": float(nodes[:, 2].max()),
        },
    }


def cgns_to_bdf(cgns_path: str, bdf_path: str | None = None,
                limit: int | None = None) -> dict[str, Any]:
    """把 CGNS/HDF5 的网格写成自由格式 Nastran BDF。"""
    from .cgns import CgnsFile, _SURFACE_ELEMS, _VOLUME_ELEMS

    cgns_path = os.path.abspath(cgns_path)
    if bdf_path is None:
        bdf_path = os.path.splitext(cgns_path)[0] + ".bdf"

    coords_parts: list[np.ndarray] = []
    elem_parts: list[tuple[str, np.ndarray]] = []
    offset = 0
    cg = CgnsFile(cgns_path)
    try:
        for _bname, base in cg.bases():
            for _zname, zone in cg.zones(base):
                xyz, elems = cg.zone_arrays(zone)
                if xyz.size == 0:
                    continue
                coords_parts.append(xyz)
                # 有体单元时，表面单元是边界表示而非"单元"，不写入 BDF，避免重复计数
                has_volume = any(t in _VOLUME_ELEMS for t, _ in elems)
                for etype, conn in elems:
                    if has_volume and etype in _SURFACE_ELEMS:
                        continue
                    elem_parts.append((etype, conn + offset))
                offset += len(xyz)
    finally:
        cg.close()
    if not coords_parts:
        raise ValueError("CGNS 中没有可导出的坐标")

    nodes = np.vstack(coords_parts)
    total = sum(len(c) for _, c in elem_parts)
    if limit:
        remain = limit
        capped = []
        for etype, conn in elem_parts:
            if remain <= 0:
                break
            capped.append((etype, conn[:remain]))
            remain -= len(conn[:remain])
        elem_parts = capped
        total = sum(len(c) for _, c in elem_parts)

    with open(bdf_path, "w", encoding="utf-8") as f:
        f.write("SOL 101\nCEND\nBEGIN BULK\n")
        for i, (x, y, z) in enumerate(nodes, start=1):
            f.write(f"GRID,{i},0,{x:.9g},{y:.9g},{z:.9g}\n")
        eid = 1
        for etype, conn in elem_parts:
            card, _npe = CGNS_TO_BDF.get(etype, (None, 0))
            if card is None:
                continue
            for row in conn:
                f.write(card + f",{eid},1," + ",".join(str(int(n) + 1) for n in row) + "\n")
                eid += 1
        f.write("ENDDATA\n")

    return {
        "source": cgns_path,
        "target": bdf_path,
        "nodes": len(nodes),
        "elements": eid - 1,
        "truncated": bool(limit and eid - 1 < total),
    }
