"""写入 CGNS/HDF5（最小可用子集），用于跨格式转换与一致性验证。

只写：单 Base、单 Zone（Unstructured）、坐标、按类型分组的 Elements_t。
节点命名遵循 CGNS/HDF5 映射：标量值放在名为 " data" 的子节点（带前导空格）。
"""

from __future__ import annotations

import numpy as np
import h5py

_CTYPE = {"I4": "i4", "R4": "f4", "R8": "f8", "C1": "u1"}


def _node(parent: h5py.Group, name: str, label: str, dtype: str, data: np.ndarray | bytes) -> h5py.Group:
    """创建一个 CGNS 实体节点，并把值写入 ' data'。"""
    g = parent.create_group(name)
    g.attrs["label"] = np.bytes_(label.encode())
    g.attrs["name"] = np.bytes_(name.encode())
    g.attrs["type"] = np.bytes_(dtype.encode())
    g.attrs["flags"] = np.array([0], dtype="i4")
    arr = np.frombuffer(data, dtype="u1") if isinstance(data, (bytes, bytearray)) else np.asarray(data)
    g.create_dataset(" data", data=arr.astype(_CTYPE.get(dtype, "i4")))
    return g


def write_cgns(
    path: str,
    nodes: np.ndarray,
    elements: list[dict],
    zone_name: str = "Zone1",
    base_name: str = "Base",
    version: float = 4.1,
) -> str:
    """写一个非结构 CGNS/HDF5。

    nodes: (N,3) 浮点坐标；elements: [{"type": "TETRA_4", "conn": np.ndarray(M,4) 0-based}]
    """
    nodes = np.asarray(nodes, dtype=float)
    if nodes.ndim != 2 or nodes.shape[1] != 3:
        raise ValueError("nodes 必须是 (N,3) 数组")

    groups: dict[str, list[np.ndarray]] = {}
    for e in elements:
        conn = np.asarray(e["conn"], dtype=np.int32)
        if conn.size == 0:
            continue
        groups.setdefault(e["type"], []).append(conn)
    n_elem = sum(len(c) for arrs in groups.values() for c in arrs)

    with h5py.File(path, "w") as f:
        f.attrs["label"] = np.bytes_(b"Root Node of HDF5 File")
        f.attrs["name"] = np.bytes_(b"HDF5 MotherNode")
        f.attrs["type"] = np.bytes_(b"MT")
        _node(f, "CGNSLibraryVersion", "CGNSLibraryVersion_t", "R4", np.array([version], dtype="f4"))

        base = f.create_group(base_name)
        base.attrs["label"] = np.bytes_(b"CGNSBase_t")
        base.attrs["name"] = np.bytes_(base_name.encode())
        base.attrs["type"] = np.bytes_(b"I4")
        base.attrs["flags"] = np.array([0], dtype="i4")
        base.create_dataset(" data", data=np.array([3, 3], dtype="i4"))

        zone = base.create_group(zone_name)
        zone.attrs["label"] = np.bytes_(b"Zone_t")
        zone.attrs["name"] = np.bytes_(zone_name.encode())
        zone.attrs["type"] = np.bytes_(b"I4")
        zone.attrs["flags"] = np.array([0], dtype="i4")
        zone.create_dataset(" data", data=np.array([[len(nodes)], [n_elem], [len(nodes)]], dtype="i4"))

        _node(zone, "ZoneType", "ZoneType_t", "C1", b"Unstructured")

        gc = zone.create_group("GridCoordinates")
        gc.attrs["label"] = np.bytes_(b"GridCoordinates_t")
        gc.attrs["name"] = np.bytes_(b"GridCoordinates")
        gc.attrs["type"] = np.bytes_(b"MT")
        gc.attrs["flags"] = np.array([0], dtype="i4")
        for i, axis in enumerate("XYZ"):
            _node(gc, f"Coordinate{axis}", "DataArray_t", "R8", nodes[:, i])

        start = 1
        for etype, arrs in groups.items():
            conn = np.vstack(arrs).astype(np.int32) + 1  # CGNS 为 1-based
            count = int(conn.shape[0])
            g = zone.create_group(f"GridElements_{etype}")
            g.attrs["label"] = np.bytes_(b"Elements_t")
            g.attrs["name"] = np.bytes_(f"GridElements_{etype}".encode())
            g.attrs["type"] = np.bytes_(b"I4")
            g.attrs["flags"] = np.array([0], dtype="i4")
            _node(g, "ElementRange", "IndexRange_t", "I4", np.array([start, start + count - 1], dtype="i4"))
            _node(g, "ElementConnectivity", "DataArray_t", "I4", conn.ravel())
            start += count
    return path
