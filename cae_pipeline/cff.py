"""ANSYS Fluent CFF（``.cas.h5`` / ``.dat.h5``）读取核心。

文件布局（Fluent 2025R1 导出，ParaView/Ensight 兼容）::

    .cas.h5
      /meshes/1              @nodeCount @faceCount @cellCount @dimension
        /nodes/coords/<sec>  float64 (n_nodes, 3)
        /nodes/zoneTopology
        /faces/nodes/<sec>/{nodes(uint32), nnodes(int16)}   节点连接（1-based 扁平）
        /faces/c0/<sec>, /faces/c1/<sec>                    两侧单元（0 = 无）
        /faces/zoneTopology  id/name/faceType/zoneType/minId/maxId/...
        /cells/ctype/<sec>/cell-types                       单元类型码
        /cells/zoneTopology
      /settings/<name>       Scheme 文本（求解设置，见 scheme.py）

    .dat.h5
      /results/1/phase-<k>/{cells,faces}/SV_<VAR>/<sec>     结果场（1-based 全局 id）
      /settings/*            版本 / 流时间 / 时间步

本模块只做「文件 -> numpy 数组/字典」的机械读取，语义解释放在 setup/mesh/fields。
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterator

import h5py
import numpy as np

# Fluent 面/单元 zone 类型码。数据集内已核对：1=fluid, 2=interior, 3=wall, 10=velocity-inlet
# （与 /settings/Thread Variables 中的 kind 文本一致）；其余为常见取值，读取时优先采用
# Thread Variables 的文本类型，本表仅作兜底。
ZONE_TYPE_LABELS: dict[int, str] = {
    0: "dead",
    1: "fluid",
    2: "interior",
    3: "wall",
    4: "pressure-inlet",
    5: "pressure-outlet",
    6: "symmetry",
    7: "periodic",
    8: "periodic-shadow",
    9: "exhaust-fan",
    10: "velocity-inlet",
    11: "intake-fan",
    12: "mass-flow-inlet",
    13: "interface",
    14: "outflow",
    15: "axis",
    16: "mass-flow-outlet",
    17: "pressure-far-field",
    18: "inlet-vent",
    19: "outlet-vent",
    20: "solid",
}


def zone_label(code: int) -> str:
    return ZONE_TYPE_LABELS.get(int(code), f"zone-type-{int(code)}")


@dataclasses.dataclass
class Zone:
    """面 zone 或 单元 zone。"""

    location: str          # "faces" | "cells" | "nodes"
    zone_id: int
    name: str
    zone_type: int
    min_id: int
    max_id: int
    face_type: int | None = None
    cell_type: int | None = None
    c0: int | None = None
    c1: int | None = None
    shadow_zone_id: int | None = None
    child_zone_id: int | None = None

    @property
    def count(self) -> int:
        return int(self.max_id - self.min_id + 1)

    @property
    def type_label(self) -> str:
        return zone_label(self.zone_type)

    @property
    def is_boundary(self) -> bool:
        return self.location == "faces" and self.c1 == 0

    def to_dict(self, bctype: str | None = None) -> dict[str, Any]:
        out = {
            "id": self.zone_id,
            "name": self.name,
            "type": bctype or self.type_label,
            "type_code": int(self.zone_type),
            "count": self.count,
        }
        if self.face_type is not None:
            out["face_type_code"] = int(self.face_type)
        if self.c0 is not None:
            out["adjacent_cell_zone"] = int(self.c0)
        if self.c1 is not None:
            out["opposite_cell_zone"] = int(self.c1)
        if self.shadow_zone_id:
            out["shadow_zone_id"] = int(self.shadow_zone_id)
        return out


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _scalar(ds: h5py.Dataset) -> Any:
    arr = np.asarray(ds)
    if arr.size == 1:
        item = arr.reshape(-1)[0]
    else:
        item = arr
    return item


class FluentCffBase:
    """公共读取工具：分段数据集 -> 全局 1-based id 数组。"""

    def __init__(self, h5: h5py.File):
        self.h5 = h5
        self.version = self._settings_text("Version")
        self.solver = self._settings_text("Solver")
        self.origin = self._settings_text("Origin")

    # --- settings -----------------------------------------------------
    @property
    def settings_names(self) -> list[str]:
        if "settings" not in self.h5:
            return []
        return sorted(self.h5["settings"].keys())

    def _settings_text(self, name: str) -> str | None:
        if "settings" not in self.h5 or name not in self.h5["settings"]:
            return None
        return _decode(_scalar(self.h5["settings"][name][0]))

    def settings_text(self, name: str) -> str | None:
        return self._settings_text(name)

    # --- 分段读取 -----------------------------------------------------
    @staticmethod
    def _sections(group: h5py.Group) -> Iterator[tuple[int, int, Any]]:
        """遍历分段容器：子节点可能是 dataset（coords/c0）或 group（faces/nodes、ctype）。"""
        for key in sorted(group.keys()):
            obj = group[key]
            if isinstance(obj, h5py.Group):
                if "minId" not in obj.attrs or "maxId" not in obj.attrs:
                    continue
                yield int(obj.attrs["minId"]), int(obj.attrs["maxId"]), obj
            elif isinstance(obj, h5py.Dataset):
                lo = int(obj.attrs.get("minId", 1))
                hi = int(obj.attrs.get("maxId", obj.shape[0] if obj.ndim else 1))
                yield lo, hi, obj

    @staticmethod
    def _section_array(obj: Any, name: str | None = None) -> np.ndarray:
        """从分段节点取出数据：group 需要指定内层 dataset 名，dataset 直接用。"""
        if isinstance(obj, h5py.Group):
            if name is None:
                name = next(iter(obj))
            return np.asarray(obj[name])
        return np.asarray(obj)

    def _gather(self, group: h5py.Group, total: int, width: int | None = None,
                fill: float = np.nan) -> np.ndarray:
        """把带 minId/maxId 的分段数据集拼成按全局 id 对齐的数组。"""
        ref = next(iter(self._sections(group)), None)
        if ref is None:
            shape = (total,) if width is None else (total, width)
            return np.full(shape, fill)
        ref_arr = self._section_array(ref[2])
        shape = (total,) if ref_arr.ndim == 1 else (total, ref_arr.shape[1])
        out = np.full(shape, fill, dtype=np.float64)
        for lo, hi, obj in self._sections(group):
            arr = self._section_array(obj)
            if arr.ndim == 1:
                arr = arr.reshape(-1)
            if hi > total:
                hi = min(hi, lo + arr.shape[0] - 1)
            out[lo - 1:hi] = arr[: hi - lo + 1]
        return out


class FluentCase(FluentCffBase):
    """``.cas.h5``：网格 + 设置。"""

    def __init__(self, h5: h5py.File, mesh_key: str = "1"):
        super().__init__(h5)
        if "meshes" not in h5:
            raise ValueError("不是 Fluent case 文件（缺少 /meshes）")
        self.mesh_key = mesh_key if mesh_key in h5["meshes"] else next(iter(h5["meshes"]))
        self.mesh = h5["meshes"][self.mesh_key]

    # --- 计数 ---------------------------------------------------------
    @property
    def node_count(self) -> int:
        return int(np.asarray(self.mesh.attrs["nodeCount"]).reshape(-1)[0])

    @property
    def face_count(self) -> int:
        return int(np.asarray(self.mesh.attrs["faceCount"]).reshape(-1)[0])

    @property
    def cell_count(self) -> int:
        return int(np.asarray(self.mesh.attrs["cellCount"]).reshape(-1)[0])

    @property
    def dimension(self) -> int:
        return int(np.asarray(self.mesh.attrs["dimension"]).reshape(-1)[0])

    def counts(self, location: str) -> int:
        return {"nodes": self.node_count, "faces": self.face_count, "cells": self.cell_count}[location]

    # --- 原始数组 -----------------------------------------------------
    def coordinates(self) -> np.ndarray:
        """节点坐标 (n_nodes, 3)，1-based 顺序。"""
        group = self.mesh["nodes/coords"]
        out = np.empty((self.node_count, 3), dtype=np.float64)
        for lo, hi, obj in self._sections(group):
            arr = self._section_array(obj).astype(np.float64, copy=False)
            out[lo - 1:hi] = arr[: hi - lo + 1]
        return out

    def face_nodes(self) -> tuple[np.ndarray, np.ndarray]:
        """返回 (扁平节点索引 int32, 每个面的节点数 int16)，均为 0-based 节点索引。"""
        group = self.mesh["faces/nodes"]
        flats: list[np.ndarray] = []
        counts: list[np.ndarray] = []
        for _lo, _hi, sec in self._sections(group):
            flats.append(np.asarray(sec["nodes"], dtype=np.int32) - 1)
            counts.append(np.asarray(sec["nnodes"], dtype=np.int16))
        return np.concatenate(flats), np.concatenate(counts)

    def face_cells(self, side: int) -> np.ndarray:
        group = self.mesh[f"faces/c{side}"]
        out = np.zeros(self.face_count, dtype=np.int64)
        for lo, hi, obj in self._sections(group):
            out[lo - 1:hi] = self._section_array(obj)
        return out

    def cell_types(self) -> np.ndarray:
        """单元类型码。CFF 中「同类型段」不存 cell-types，改用段属性 elementType。"""
        group = self.mesh["cells/ctype"]
        out = np.zeros(self.cell_count, dtype=np.int32)
        for lo, hi, sec in self._sections(group):
            if isinstance(sec, h5py.Group) and "cell-types" in sec:
                out[lo - 1:hi] = np.asarray(sec["cell-types"])
            else:
                attrs = sec.attrs if isinstance(sec, h5py.Group) else getattr(sec, "attrs", {})
                element_type = int(np.asarray(attrs.get("elementType", 0)).reshape(-1)[0]) if attrs else 0
                out[lo - 1:hi] = element_type
        return out

    # --- zone 表 ------------------------------------------------------
    def zones(self, location: str) -> list[Zone]:
        group = self.mesh[f"{location}/zoneTopology"]
        if not isinstance(group, h5py.Group):
            return []
        def col(key: str) -> np.ndarray | None:
            return np.asarray(group[key]) if key in group else None

        ids = col("id")
        names = col("name")
        zone_type = col("zoneType")
        min_id, max_id = col("minId"), col("maxId")
        face_type, cell_type = col("faceType"), col("cellType")
        c0, c1 = col("c0"), col("c1")
        shadow, child, dim = col("shadowZoneId"), col("childZoneId"), col("dimension")
        # name 列可能被压成单串（分号分隔）
        name_list: list[str] = []
        if names is not None:
            if names.shape[0] == 1 and ids is not None and ids.shape[0] > 1:
                name_list = _decode(names[0]).split(";")
            else:
                name_list = [_decode(n) for n in names]
        out: list[Zone] = []
        n = int(zone_type.shape[0]) if zone_type is not None else 0
        for i in range(n):
            out.append(
                Zone(
                    location=location,
                    zone_id=int(ids[i]),
                    name=name_list[i] if i < len(name_list) else f"zone-{int(ids[i])}",
                    zone_type=int(zone_type[i]),
                    min_id=int(min_id[i]),
                    max_id=int(max_id[i]),
                    face_type=int(face_type[i]) if face_type is not None else None,
                    cell_type=int(cell_type[i]) if cell_type is not None else None,
                    c0=int(c0[i]) if c0 is not None else None,
                    c1=int(c1[i]) if c1 is not None else None,
                    shadow_zone_id=int(shadow[i]) if shadow is not None else None,
                    child_zone_id=int(child[i]) if child is not None else None,
                )
            )
        return out

    def face_zones(self) -> list[Zone]:
        return self.zones("faces")

    def cell_zones(self) -> list[Zone]:
        return self.zones("cells")

    def node_zones(self) -> list[Zone]:
        return self.zones("nodes")

    def zone_by_name(self, name: str, location: str = "faces") -> Zone | None:
        for z in self.zones(location):
            if z.name == name:
                return z
        return None

    def zone_by_id(self, zone_id: int, location: str = "faces") -> Zone | None:
        for z in self.zones(location):
            if z.zone_id == zone_id:
                return z
        return None


class FluentData(FluentCffBase):
    """``.dat.h5``：结果场。"""

    def __init__(self, h5: h5py.File, result_key: str = "1"):
        super().__init__(h5)
        if "results" not in h5:
            raise ValueError("不是 Fluent data 文件（缺少 /results）")
        self.result_key = result_key if result_key in h5["results"] else next(iter(h5["results"]))
        self.results = h5["results"][self.result_key]
        self.case_file = self._settings_text("Case File")

    # --- 时间信息 -----------------------------------------------------
    @property
    def data_variables(self) -> str | None:
        return self._settings_text("Data Variables")

    @property
    def flow_time(self) -> float | None:
        from .scheme import find_key, parse

        text = self.data_variables
        if not text:
            return None
        value = find_key(parse(text), "flow-time")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def time_step(self) -> int | None:
        from .scheme import find_key, parse

        text = self.data_variables
        if not text:
            return None
        value = find_key(parse(text), "time-step")
        return int(value) if isinstance(value, (int, float)) else None

    # --- 相 -----------------------------------------------------------
    def phases(self) -> list[int]:
        out = []
        for key in sorted(self.results.keys()):
            obj = self.results[key]
            if isinstance(obj, h5py.Group) and key.startswith("phase"):
                out.append(int(obj.attrs.get("phaseId", key.split("-")[-1])))
        return sorted(out)

    def _phase_group(self, phase: int, location: str) -> h5py.Group | None:
        for key in self.results:
            obj = self.results[key]
            if isinstance(obj, h5py.Group) and int(obj.attrs.get("phaseId", -1)) == int(phase):
                return obj[location] if location in obj else None
        return None

    def field_names(self, location: str = "cells", phase: int = 1) -> list[str]:
        group = self._phase_group(phase, location)
        if group is None:
            return []
        return sorted(k for k in group.keys() if k.startswith("SV_"))

    def field_catalog(self) -> dict[str, dict[str, list[str]]]:
        out: dict[str, dict[str, list[str]]] = {}
        for phase in self.phases():
            out[f"phase-{phase}"] = {
                loc: self.field_names(loc, phase) for loc in ("cells", "faces")
            }
        return out

    def read_field(self, name: str, location: str = "cells", phase: int = 1,
                   total: int | None = None) -> np.ndarray:
        """读取结果场，按全局 id 对齐（缺失段填 NaN）。"""
        group = self._phase_group(phase, location)
        if group is None or name not in group:
            raise KeyError(f"{location}/phase-{phase} 中不存在场 {name}")
        if total is None:
            total = max(hi for _lo, hi, _ds in self._sections(group[name]))
        return self._gather(group[name], total)

    def field_sections(self, name: str, location: str = "cells", phase: int = 1) -> list[tuple[int, int, int, int]]:
        """返回 [(min_id, max_id, count, width)]，用于判断「部分存盘」的场。"""
        group = self._phase_group(phase, location)
        if group is None or name not in group:
            return []
        out = []
        for lo, hi, obj in self._sections(group[name]):
            arr = self._section_array(obj)
            width = int(arr.shape[1]) if arr.ndim == 2 else 1
            out.append((lo, hi, int(arr.shape[0]), width))
        return out
